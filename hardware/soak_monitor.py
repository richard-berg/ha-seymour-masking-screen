#!/usr/bin/env python3
"""Watch a Seymour masking screen in Home Assistant over days or weeks.

Every interval it records, to a CSV file:

- the state of the status sensor and of the "all masks" cover, read through
  Home Assistant's REST API (a long-lived access token is needed),
- when the controller last answered (the "last contact" sensor; enable it),
- how many TCP connections this host has open to the IP2SL's port, from
  /proc/net/tcp. Run the monitor on the Home Assistant host for this one:
  more than one connection means one leaked, and the IP2SL allows only one.

On Ctrl-C it prints a summary: availability, the longest outage, and the
most connections seen. Standard library only.

  soak_monitor.py --url http://homeassistant.local:8123 --token TOKEN \\
      --device seymour_prh_123 --bridge-port 4999 --out soak.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from urllib import error, request

UNAVAILABLE = {"unavailable", "unknown", None}
ESTABLISHED = "01"


def get_state(url: str, token: str, entity_id: str) -> str | None:
    """Return the state of an entity, or None if it can't be read."""
    req = request.Request(
        f"{url}/api/states/{entity_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with request.urlopen(req, timeout=10) as response:
            return str(json.load(response)["state"])
    except (error.URLError, TimeoutError, KeyError, ValueError):
        return None


def count_connections(port: int) -> int:
    """Count established TCP connections from this host to the given port."""
    count = 0
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        path = Path(table)
        if not path.exists():
            continue
        for line in path.read_text().splitlines()[1:]:
            parts = line.split()
            remote_port = int(parts[2].rsplit(":", 1)[1], 16)
            if remote_port == port and parts[3] == ESTABLISHED:
                count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", required=True, help="Home Assistant URL")
    parser.add_argument("--token", required=True, help="long-lived access token")
    parser.add_argument(
        "--device", required=True, help="entity ID prefix, e.g. seymour_prh_123"
    )
    parser.add_argument("--bridge-port", type=int, default=4999)
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--out", default="soak.csv")
    args = parser.parse_args()

    entities = {
        "status": f"sensor.{args.device}_status",
        "cover": f"cover.{args.device}_all_masks",
        "last_contact": f"sensor.{args.device}_last_contact",
    }
    samples = 0
    available = 0
    outage_start: float | None = None
    longest_outage = 0.0
    most_connections = 0
    started = time.monotonic()

    with Path(args.out).open("a", newline="") as file:
        writer = csv.writer(file)
        if file.tell() == 0:
            writer.writerow(["time", *entities, "connections"])
        try:
            while True:
                now = time.monotonic()
                states = {
                    key: get_state(args.url, args.token, entity)
                    for key, entity in entities.items()
                }
                connections = count_connections(args.bridge_port)
                writer.writerow(
                    [time.strftime("%Y-%m-%dT%H:%M:%S"), *states.values(), connections]
                )
                file.flush()

                samples += 1
                most_connections = max(most_connections, connections)
                if states["cover"] in UNAVAILABLE:
                    outage_start = outage_start or now
                    longest_outage = max(longest_outage, now - outage_start)
                else:
                    available += 1
                    outage_start = None
                if connections > 1:
                    print(f"{time.strftime('%H:%M:%S')} {connections} connections!")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass

    hours = (time.monotonic() - started) / 3600
    print(f"\n{samples} samples over {hours:.1f} h")
    if samples:
        print(f"available: {100 * available / samples:.2f}%")
    print(f"longest outage: {longest_outage / 60:.1f} min")
    print(f"most connections to port {args.bridge_port}: {most_connections}")


if __name__ == "__main__":
    main()
