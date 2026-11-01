"""Repairs for the Seymour Masking Screen integration."""

from typing import Any, override

from seymourlib.exceptions import SeymourError
from seymourlib.itach import set_serial_settings

from homeassistant.components.repairs import (
    ConfirmRepairFlow,
    RepairsFlow,
    RepairsFlowResult,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant

from .bridge import ip2sl_host
from .coordinator import SeymourConfigEntry


class BridgeSettingsRepairFlow(ConfirmRepairFlow):
    """Set the serial port of an IP2SL up for the controller."""

    def __init__(self, entry: SeymourConfigEntry) -> None:
        """Initialize the flow."""
        self._entry = entry

    @override
    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Change the settings once the user confirms."""
        if user_input is None:
            return await super().async_step_confirm()
        host = ip2sl_host(self._entry.data[CONF_DEVICE])
        assert host is not None
        try:
            await set_serial_settings(host)
        except SeymourError:
            return self.async_abort(reason="cannot_connect")
        if self._entry.state is ConfigEntryState.LOADED:
            await self._entry.runtime_data.status.async_request_refresh()
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create a flow to fix an issue."""
    assert data is not None
    entry = hass.config_entries.async_get_entry(data["entry_id"])
    assert entry is not None
    return BridgeSettingsRepairFlow(entry)
