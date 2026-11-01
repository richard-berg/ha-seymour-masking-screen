"""Diagnostics support for the Seymour Masking Screen integration."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant

from .coordinator import SeymourConfigEntry

# The serial number can contain the owner's name, and an ESPHome proxy URL can
# contain an API key.
TO_REDACT = {"serial_number", "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SeymourConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = entry.runtime_data
    status = runtime_data.status.data
    metadata = runtime_data.metadata.data
    system_info = metadata.system_info
    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "unique_id": entry.unique_id,
                CONF_DEVICE: entry.data[CONF_DEVICE].split("?", 1)[0],
            },
            "system_info": {
                "screen_model": system_info.screen_model,
                "serial_number": system_info.serial_number.to_serial_number(),
                "width_inches": system_info.width_inches,
                "height_inches": system_info.height_inches,
                "mask_ids": [mask.name for mask in system_info.mask_ids],
            },
            "ratios": [
                {
                    "id": setting.ratio.id,
                    "label": setting.label,
                    "width_inches": setting.width_inches,
                    "height_inches": setting.height_inches,
                    "motor_positions_pct": setting.motor_positions_pct,
                    "motor_adjustments_pct": setting.motor_adjustments_pct,
                }
                for setting in metadata.ratios.values()
            ],
            "status": {
                "code": status.status.code.name,
                "ratio": status.status.ratio.id if status.status.ratio else None,
                "positions": {
                    mask.name: position for mask, position in status.positions.items()
                },
            },
            "connection": runtime_data.client.connection_stats,
        },
        TO_REDACT,
    )
