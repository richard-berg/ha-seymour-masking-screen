"""The Seymour Masking Screen integration."""

from seymourlib.client import SeymourClient
from seymourlib.exceptions import SeymourError
from seymourlib.transport import URLTransport

from homeassistant.const import CONF_DEVICE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import (
    ISSUE_UNREACHABLE,
    SeymourConfigEntry,
    SeymourMetadataCoordinator,
    SeymourRuntimeData,
    SeymourStatusCoordinator,
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
        await client.close()
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
        await client.close()
        raise

    entry.runtime_data = SeymourRuntimeData(
        client=client, status=status, metadata=metadata
    )
    entry.async_on_unload(client.close)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(hass: HomeAssistant, entry: SeymourConfigEntry) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_UNREACHABLE}_{entry.entry_id}")
