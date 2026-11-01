#!/usr/bin/env python3
"""Talk raw bytes to a Seymour controller through an IP2SL, with timestamps.

Every byte sent and received is logged with the milliseconds since the
start, so the timing the integration relies on can be measured: how long
answers take, whether commands get any reply, and how soon the status shows
that masks move. Standard library only.

Disable the Home Assistant entry first: the IP2SL allows one connection.

Frames are given with Python escapes, e.g. '[01S]' or '[01S]\\r\\n' or
'\\x00\\xff'. Examples:

  raw_console.py HOST send '[01S]' '[01P]' --listen 3
  raw_console.py HOST send '[01HA]' --listen 5              # any reply to a command?
  raw_console.py HOST send '[01S][01P][01S]' --listen 2     # pipelined in one write
  raw_console.py HOST bench '[01S]' --count 50               # latency statistics
  raw_console.py HOST watch '[01AA]' --seconds 30            # status after a command
  raw_console.py HOST second-connection                     # does a 2nd client work?
  raw_console.py HOST churn --count 1000                     # connect/ask/close loop
  raw_console.py HOST interactive
"""

from __future__ import annotations

import argparse
import asyncio
import codecs
import contextlib
import statistics
import sys
import time

STATUS = b"[01S]"
START = time.monotonic()


def stamp() -> str:
    return f"{(time.monotonic() - START) * 1000:10.1f} ms"


def log(direction: str, data: bytes) -> None:
    print(f"{stamp()}  {direction}  {data!r}", flush=True)


def parse_frame(text: str) -> bytes:
    """Turn a command-line frame with Python escapes into bytes."""
    return codecs.decode(text, "unicode_escape").encode("latin-1")


async def connect(
    args: argparse.Namespace,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    async with asyncio.timeout(args.connect_timeout):
        return await asyncio.open_connection(args.host, args.port)


async def read_frame(reader: asyncio.StreamReader, seconds: float) -> bytes | None:
    """Return the next bytes up to ']', or None if none come in time."""
    try:
        async with asyncio.timeout(seconds):
            return await reader.readuntil(b"]")
    except TimeoutError:
        return None


async def listen(reader: asyncio.StreamReader, seconds: float) -> bytes:
    """Log everything that arrives for the given time; return it."""
    received = b""
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            async with asyncio.timeout(remaining):
                chunk = await reader.read(4096)
        except TimeoutError:
            break
        if not chunk:
            log("EOF", b"")
            break
        log("RX ", chunk)
        received += chunk
    return received


async def listen_quiet(reader: asyncio.StreamReader, seconds: float) -> bytes:
    """Return what arrives within the given time, without logging it."""
    data = b""
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(seconds):
            while chunk := await reader.read(4096):
                data += chunk
    return data


async def cmd_send(args: argparse.Namespace) -> None:
    reader, writer = await connect(args)
    try:
        for i, text in enumerate(args.frames):
            if i and args.gap:
                await asyncio.sleep(args.gap)
            frame = parse_frame(text)
            log("TX ", frame)
            writer.write(frame)
            await writer.drain()
            if args.listen_each:
                await listen(reader, args.listen_each)
        await listen(reader, args.listen)
    finally:
        writer.close()


async def cmd_bench(args: argparse.Namespace) -> None:
    reader, writer = await connect(args)
    frame = parse_frame(args.frame)
    latencies: list[float] = []
    timeouts = 0
    responses: dict[bytes, int] = {}
    try:
        for _ in range(args.count):
            start = time.monotonic()
            writer.write(frame)
            await writer.drain()
            response = await read_frame(reader, args.timeout)
            if response is None:
                timeouts += 1
                print(f"{stamp()}  timeout", flush=True)
            else:
                latencies.append((time.monotonic() - start) * 1000)
                responses[response] = responses.get(response, 0) + 1
            # Anything after the frame, such as a CR/LF terminator?
            if trailing := await listen_quiet(reader, args.trailing):
                print(f"{stamp()}  bytes after the frame: {trailing!r}", flush=True)
            await asyncio.sleep(args.interval)
    finally:
        writer.close()
    print(f"\n{len(latencies)} answers, {timeouts} timeouts")
    if latencies:
        latencies.sort()
        p99 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.99))]
        print(
            f"latency ms: min {latencies[0]:.1f}  "
            f"median {statistics.median(latencies):.1f}  "
            f"p99 {p99:.1f}  max {latencies[-1]:.1f}"
        )
    for response, count in sorted(responses.items(), key=lambda item: -item[1]):
        print(f"  {count:5d} x {response!r}")


async def cmd_watch(args: argparse.Namespace) -> None:
    """Send a command, then poll the status, logging each change."""
    reader, writer = await connect(args)
    try:
        frame = parse_frame(args.frame)
        log("TX ", frame)
        sent = time.monotonic()
        writer.write(frame)
        await writer.drain()
        last: bytes | None = None
        while time.monotonic() - sent < args.seconds:
            writer.write(STATUS)
            await writer.drain()
            status = await read_frame(reader, args.timeout)
            if status != last:
                elapsed = (time.monotonic() - sent) * 1000
                print(f"{stamp()}  +{elapsed:8.1f} ms after the command: {status!r}")
                last = status
            await asyncio.sleep(args.every)
    finally:
        writer.close()


async def cmd_second_connection(args: argparse.Namespace) -> None:
    """Find out what an IP2SL does with a second client of its serial port."""
    reader_a, writer_a = await connect(args)
    print("connection A open")
    writer_a.write(STATUS)
    print(f"A answers: {await read_frame(reader_a, args.timeout)!r}")
    try:
        reader_b, writer_b = await connect(args)
    except (OSError, TimeoutError) as err:
        print(f"connection B refused or timed out: {err!r}")
        writer_a.close()
        return
    print("connection B open")
    writer_b.write(STATUS)
    print(f"B answers: {await read_frame(reader_b, args.timeout)!r}")
    print(f"A got meanwhile: {await listen_quiet(reader_a, 1)!r}")
    try:
        writer_a.write(STATUS)
        await writer_a.drain()
        print(f"A still answers: {await read_frame(reader_a, args.timeout)!r}")
    except OSError as err:
        print(f"A was dropped: {err!r}")
    print(f"B got meanwhile: {await listen_quiet(reader_b, 1)!r}")
    writer_a.close()
    writer_b.close()


async def cmd_churn(args: argparse.Namespace) -> None:
    """Connect, ask for the status, and disconnect, many times."""
    failures = 0
    times: list[float] = []
    for i in range(1, args.count + 1):
        start = time.monotonic()
        try:
            reader, writer = await connect(args)
            writer.write(STATUS)
            if await read_frame(reader, args.timeout) is None:
                raise TimeoutError("no answer")
            writer.close()
            await writer.wait_closed()
            times.append((time.monotonic() - start) * 1000)
        except (OSError, TimeoutError) as err:
            failures += 1
            print(f"{stamp()}  cycle {i}: {err!r}", flush=True)
        if i % 100 == 0:
            print(f"{stamp()}  {i} cycles, {failures} failures", flush=True)
        await asyncio.sleep(args.interval)
    print(f"\n{args.count} cycles, {failures} failures")
    if times:
        print(
            f"connect+answer ms: median {statistics.median(times):.1f}  "
            f"max {max(times):.1f}"
        )


async def cmd_interactive(args: argparse.Namespace) -> None:
    reader, writer = await connect(args)

    async def pump() -> None:
        while chunk := await reader.read(4096):
            log("RX ", chunk)
        log("EOF", b"")

    receiving = asyncio.create_task(pump())
    loop = asyncio.get_running_loop()
    print("Type frames with Python escapes, e.g. [01S]; an empty line quits")
    try:
        while line := (await loop.run_in_executor(None, sys.stdin.readline)).rstrip(
            "\n"
        ):
            frame = parse_frame(line)
            log("TX ", frame)
            writer.write(frame)
            await writer.drain()
    finally:
        receiving.cancel()
        writer.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=4999)
    parser.add_argument("--connect-timeout", type=float, default=5)
    parser.add_argument(
        "--timeout", type=float, default=3, help="seconds to wait for an answer"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    send = commands.add_parser("send", help="send frames, log what comes back")
    send.add_argument("frames", nargs="+")
    send.add_argument("--listen", type=float, default=3, help="seconds to listen")
    send.add_argument(
        "--listen-each", type=float, default=0, help="seconds to listen per frame"
    )
    send.add_argument("--gap", type=float, default=0, help="seconds between frames")
    send.set_defaults(func=cmd_send)

    bench = commands.add_parser("bench", help="measure answer latency")
    bench.add_argument("frame")
    bench.add_argument("--count", type=int, default=50)
    bench.add_argument("--interval", type=float, default=0.2)
    bench.add_argument(
        "--trailing",
        type=float,
        default=0.05,
        help="seconds to wait for bytes after each frame",
    )
    bench.set_defaults(func=cmd_bench)

    watch = commands.add_parser("watch", help="send a command, log status changes")
    watch.add_argument("frame")
    watch.add_argument("--seconds", type=float, default=30)
    watch.add_argument("--every", type=float, default=0.05)
    watch.set_defaults(func=cmd_watch)

    second = commands.add_parser("second-connection", help="test a second client")
    second.set_defaults(func=cmd_second_connection)

    churn = commands.add_parser("churn", help="connect/ask/close repeatedly")
    churn.add_argument("--count", type=int, default=1000)
    churn.add_argument("--interval", type=float, default=0.1)
    churn.set_defaults(func=cmd_churn)

    interactive = commands.add_parser("interactive", help="type frames by hand")
    interactive.set_defaults(func=cmd_interactive)

    args = parser.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
