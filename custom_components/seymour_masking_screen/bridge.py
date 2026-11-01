"""Helpers for a controller behind a Global Caché IP2SL bridge."""

from seymourlib.transport import ITACH_SERIAL_PORT


def split_tcp_url(url: str) -> tuple[str, int] | None:
    """Return the host and port of a `socket://` URL, if it is one."""
    if not url.startswith("socket://"):
        return None
    host, _, port = url.removeprefix("socket://").rpartition(":")
    if not host or not port.isdigit():
        return None
    return host.strip("[]"), int(port)


def ip2sl_host(url: str) -> str | None:
    """Return the host of an IP2SL's serial port URL, if it is one.

    Only the IP2SL's own serial port has settings at its command port.
    """
    if (tcp := split_tcp_url(url)) is None or tcp[1] != ITACH_SERIAL_PORT:
        return None
    return tcp[0]
