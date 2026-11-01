#!/usr/bin/env python3
"""Print the newest Home Assistant Core release supported by this integration.

Prefers the newest stable release at or above the minimum version, and falls
back to the newest beta of it while the minimum version is not released yet.

Usage: script/pick_core_release.py <minimum version, like 2026.11.0>
"""

import re
import subprocess
import sys

TAG = re.compile(r"refs/tags/(\d+)\.(\d+)\.(\d+)(?:b(\d+))?$")


def main() -> None:
    """Print the tag to test against."""
    minimum = tuple(int(part) for part in sys.argv[1].split("."))
    tags = subprocess.run(
        ["git", "ls-remote", "--tags", "https://github.com/home-assistant/core"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    stable: list[tuple[int, ...]] = []
    beta: list[tuple[int, ...]] = []
    for line in tags.splitlines():
        if not (match := TAG.search(line)):
            continue
        version = tuple(int(part) for part in match.groups(default="-1"))
        if version[:3] < minimum:
            continue
        (beta if match.group(4) else stable).append(version)
    if not (candidates := stable or beta):
        sys.exit(f"No Home Assistant release or beta at or above {sys.argv[1]}")
    newest = max(candidates)
    tag = ".".join(str(part) for part in newest[:3])
    if newest[3] >= 0:
        tag += f"b{newest[3]}"
    print(tag)


if __name__ == "__main__":
    main()
