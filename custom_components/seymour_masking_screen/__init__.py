"""The Seymour Masking Screen integration."""

from seymourlib.client import SeymourClient
from seymourlib.exceptions import SeymourError
from seymourlib.transport import URLTransport

from homeassistant.const import CONF_DEVICE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, LOGGER
from .coordinator import (
    ISSUES,
    SeymourConfigEntry,
    SeymourMetadataCoordinator,
    SeymourRuntimeData,
    SeymourStatusCoordinator,
    issue_id,
)
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.COVER,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Seymour Masking Screen integration."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SeymourConfigEntry) -> None:
    """Set up Seymour Masking Screen from a config entry."""
    url: str = entry.data[CONF_DEVICE]
    client = SeymourClient(URLTransport(url))

    # The Global Caché IP2SL accepts a single TCP connection per serial port,
    # so the client must be closed on every failure path or later retries
    # can't connect.
    try:
        await client.connect()
    except SeymourError as err:
        await _async_release(client)
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            # An ESPHome proxy URL can carry the API key in its query string
            translation_placeholders={"device": url.split("?", 1)[0]},
        ) from err

    metadata = SeymourMetadataCoordinator(hass, entry, client)
    status = SeymourStatusCoordinator(hass, entry, client)
    try:
        await metadata.async_config_entry_first_refresh()
        await status.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await _async_release(client)
        raise

    serial_number = metadata.data.system_info.serial_number.to_serial_number()
    if entry.unique_id and serial_number != entry.unique_id:
        LOGGER.warning(
            "The controller of %s reports serial number %s, not %s. If it was "
            "replaced, its entities stay the same",
            entry.title,
            serial_number or "(none)",
            entry.unique_id,
        )

    entry.runtime_data = SeymourRuntimeData(
        client=client, status=status, metadata=metadata
    )
    entry.async_on_unload(client.close)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def _async_release(client: SeymourClient) -> None:
    """Close the client of a failed setup, without hiding why setup failed.

    An error here would replace ConfigEntryNotReady, and Home Assistant
    doesn't retry a setup that fails any other way.
    """
    try:
        await client.close()
    except Exception:  # noqa: BLE001
        LOGGER.debug("Error closing the connection after a failed setup", exc_info=True)


async def async_unload_entry(hass: HomeAssistant, entry: SeymourConfigEntry) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
    for issue in ISSUES:
        ir.async_delete_issue(hass, DOMAIN, issue_id(issue, entry.entry_id))
