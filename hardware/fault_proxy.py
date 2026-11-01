#!/usr/bin/env python3
"""A TCP proxy between Home Assistant and an IP2SL that injects faults.

Point the integration at the proxy (reconfigure it to the proxy's host and
port) and the real controller stays in the loop while the network and the
bridge misbehave on command. Standard library only.

  fault_proxy.py --upstream 192.168.1.70:4999 --listen 0.0.0.0:14999

Faults are switched at runtime through a line-based control port, with
`nc 127.0.0.1 14998` or from a script (`echo "blackhole on" | nc -q1 ...`):

  status                     show the active faults and connections
  clear                      back to a transparent proxy
  latency MS [JITTER_MS]     delay every chunk, both directions
  blackhole on|off           keep TCP up but drop every byte, both ways
  refuse on|off              close new client connections immediately
  stall on|off               stop reading from the client (backpressure)
  reset                      abort client connections with a TCP RST
  close                      close client connections with a FIN
  drop-upstream              close only the connections to the bridge
  corrupt PROBABILITY        flip one bit per device chunk with this chance
  inject BYTES               send bytes to the client as if from the device,
                             with Python escapes, e.g. inject [01P1--.]
  delay-next SECONDS         hold the next device chunk this long
  split on|off               send device bytes one at a time, 5 ms apart
  duplicate on|off           send every device chunk twice
  crlf on|off                append \\r\\n after every device chunk

Every forwarded chunk is logged with a timestamp when --verbose is given.
"""

from __future__ import annotations

import argparse
import asyncio
import codecs
import contextlib
import random
import socket
import struct
import time
from dataclasses import dataclass, field, fields

START = time.monotonic()


def stamp() -> str:
    return f"{time.monotonic() - START:10.3f}s"


@dataclass
class Faults:
    """The faults in effect; changed through the control port."""

    latency_ms: float = 0
    jitter_ms: float = 0
    blackhole: bool = False
    refuse: bool = False
    stall: bool = False
    corrupt: float = 0
    delay_next: float = 0
    split: bool = False
    duplicate: bool = False
    crlf: bool = False


@dataclass
class Session:
    """A client connection and its connection to the bridge."""

    client: asyncio.StreamWriter
    upstream: asyncio.StreamWriter
    tasks: list[asyncio.Task[None]] = field(default_factory=list)


class FaultProxy:
    """Forward bytes between clients and the bridge, faults permitting."""

    def __init__(self, upstream: tuple[str, int], verbose: bool) -> None:
        self.upstream = upstream
        self.verbose = verbose
        self.faults = Faults()
        self.sessions: list[Session] = []
        self.stall_released = asyncio.Event()
        self.stall_released.set()

    def log(self, message: str) -> None:
        print(f"{stamp()}  {message}", flush=True)

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        if self.faults.refuse:
            self.log(f"refused {peer}")
            writer.close()
            return
        try:
            up_reader, up_writer = await asyncio.open_connection(*self.upstream)
        except OSError as err:
            self.log(f"bridge unreachable for {peer}: {err!r}")
            writer.close()
            return
        self.log(f"client {peer} connected")
        session = Session(writer, up_writer)
        self.sessions.append(session)
        session.tasks = [
            asyncio.create_task(self.pump_to_device(reader, up_writer)),
            asyncio.create_task(self.pump_to_client(up_reader, writer)),
        ]
        await asyncio.wait(session.tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in session.tasks:
            task.cancel()
        for stream in (writer, up_writer):
            stream.close()
        self.sessions.remove(session)
        self.log(f"client {peer} gone")

    async def delay(self) -> None:
        faults = self.faults
        if faults.latency_ms or faults.jitter_ms:
            jitter = random.uniform(-faults.jitter_ms, faults.jitter_ms)
            await asyncio.sleep(max(0, faults.latency_ms + jitter) / 1000)

    async def pump_to_device(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        while True:
            await self.stall_released.wait()
            if not (chunk := await reader.read(4096)):
                return
            await self.delay()
            if self.faults.blackhole:
                self.log(f"dropped  -> {chunk!r}")
                continue
            if self.verbose:
                self.log(f"client   -> {chunk!r}")
            writer.write(chunk)
            await writer.drain()

    async def pump_to_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        while chunk := await reader.read(4096):
            faults = self.faults
            await self.delay()
            if faults.delay_next:
                hold, faults.delay_next = faults.delay_next, 0
                self.log(f"holding {chunk!r} for {hold}s")
                await asyncio.sleep(hold)
            if faults.blackhole:
                self.log(f"dropped  <- {chunk!r}")
                continue
            if faults.corrupt and random.random() < faults.corrupt:
                data = bytearray(chunk)
                i = random.randrange(len(data))
                data[i] ^= 1 << random.randrange(8)
                self.log(f"corrupted {chunk!r} -> {bytes(data)!r}")
                chunk = bytes(data)
            if faults.crlf:
                chunk += b"\r\n"
            if self.verbose:
                self.log(f"device   <- {chunk!r}")
            for _ in range(2 if faults.duplicate else 1):
                if faults.split:
                    for byte in chunk:
                        writer.write(bytes([byte]))
                        await writer.drain()
                        await asyncio.sleep(0.005)
                else:
                    writer.write(chunk)
                    await writer.drain()

    def control(self, line: str) -> str:
        """Apply one control command; return the reply."""
        command, _, argument = line.strip().partition(" ")
        faults = self.faults
        flag = argument.strip() == "on"
        match command:
            case "status":
                active = {
                    f.name: getattr(faults, f.name)
                    for f in fields(faults)
                    if getattr(faults, f.name) != f.default
                }
                return f"faults {active or 'none'}; {len(self.sessions)} connection(s)"
            case "clear":
                self.faults = Faults()
                self.stall_released.set()
            case "latency":
                values = [float(v) for v in argument.split()] or [0]
                faults.latency_ms = values[0]
                faults.jitter_ms = values[1] if len(values) > 1 else 0
            case "blackhole":
                faults.blackhole = flag
            case "refuse":
                faults.refuse = flag
            case "stall":
                faults.stall = flag
                if flag:
                    self.stall_released.clear()
                else:
                    self.stall_released.set()
            case "reset":
                for session in self.sessions:
                    sock = session.client.get_extra_info("socket")
                    sock.setsockopt(
                        socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                    )
                    session.client.transport.abort()
            case "close":
                for session in self.sessions:
                    session.client.close()
            case "drop-upstream":
                for session in self.sessions:
                    session.upstream.close()
            case "corrupt":
                faults.corrupt = float(argument)
            case "inject":
                data = codecs.decode(argument, "unicode_escape").encode("latin-1")
                for session in self.sessions:
                    session.client.write(data)
                return f"injected {data!r} into {len(self.sessions)} connection(s)"
            case "delay-next":
                faults.delay_next = float(argument)
            case "split":
                faults.split = flag
            case "duplicate":
                faults.duplicate = flag
            case "crlf":
                faults.crlf = flag
            case _:
                return f"unknown command {command!r}; see --help"
        self.log(f"control: {line.strip()}")
        return "ok"

    async def handle_control(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        with contextlib.suppress(ConnectionError):
            while line := (await reader.readline()).decode(errors="replace"):
                if not line.strip():
                    continue
                try:
                    reply = self.control(line)
                except (ValueError, IndexError) as err:
                    reply = f"error: {err}"
                writer.write(reply.encode() + b"\n")
                await writer.drain()
        writer.close()


def address(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    return host.strip("[]"), int(port)


async def run(args: argparse.Namespace) -> None:
    proxy = FaultProxy(address(args.upstream), args.verbose)
    server = await asyncio.start_server(proxy.handle_client, *address(args.listen))
    control = await asyncio.start_server(proxy.handle_control, *address(args.control))
    proxy.log(f"proxying {args.listen} -> {args.upstream}; control on {args.control}")
    async with server, control:
        await asyncio.gather(server.serve_forever(), control.serve_forever())


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--upstream", required=True, help="bridge HOST:PORT")
    parser.add_argument("--listen", default="0.0.0.0:14999", help="HOST:PORT")
    parser.add_argument("--control", default="127.0.0.1:14998", help="HOST:PORT")
    parser.add_argument("--verbose", action="store_true", help="log every chunk")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
