"""Select platform for the Seymour Masking Screen integration."""

import asyncio
from collections.abc import Awaitable
from typing import override

from seymourlib.exceptions import SeymourError
from seymourlib.protocol import MotorID, Ratio, RatioSetting, StatusCode

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, HOMING_POLL_INTERVAL, HOMING_START_TIMEOUT, HOMING_TIMEOUT
from .coordinator import MOVING_STATUS_CODES, SeymourConfigEntry, SeymourRuntimeData
from .entity import SeymourEntity
from .masks import is_at_home

# Requests are serialized by the client; holding commands back here would
# make a stop wait for every command before it.
PARALLEL_UPDATES = 0

# The controller reports these codes while it holds or approaches a preset.
RATIO_STATUS_CODES = frozenset(
    {StatusCode.STOPPED_AT_RATIO, StatusCode.MOVING_TO_RATIO}
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SeymourConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Seymour select entities."""
    async_add_entities([SeymourAspectRatioSelect(entry.runtime_data)])


def _is_configured(setting: RatioSetting) -> bool:
    """Return whether a ratio preset slot has been set up on the controller.

    Unused custom slots report a height of zero.
    """
    return setting.height_inches > 0


def _option(setting: RatioSetting) -> str:
    """Return the option shown for a ratio preset."""
    return f"{setting.label or setting.ratio.id} ({setting.ratio.id})"


class SeymourAspectRatioSelect(SeymourEntity, SelectEntity):
    """Select a ratio preset of the masking screen."""

    _attr_translation_key = "aspect_ratio"

    def __init__(self, runtime_data: SeymourRuntimeData) -> None:
        """Initialize the select."""
        super().__init__(runtime_data, "aspect_ratio")

    @override
    async def async_added_to_hass(self) -> None:
        """Also follow changes to the ratio presets."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.runtime_data.metadata.async_add_listener(
                self._handle_coordinator_update
            )
        )

    @property
    def _settings_by_option(self) -> dict[str, RatioSetting]:
        """Return the configured ratio presets by option."""
        return {
            _option(setting): setting
            for setting in sorted(
                self.runtime_data.metadata.data.ratios.values(),
                key=lambda setting: setting.ratio.id,
            )
            if _is_configured(setting)
        }

    @property
    @override
    def options(self) -> list[str]:
        """Return the configured ratio presets."""
        return list(self._settings_by_option)

    @property
    @override
    def current_option(self) -> str | None:
        """Return the preset the masks are at or moving to."""
        status = self.coordinator.data.status
        if status.code not in RATIO_STATUS_CODES or status.ratio is None:
            return None
        setting = self.runtime_data.metadata.data.ratios.get(status.ratio.id)
        if setting is None or not _is_configured(setting):
            return None
        return _option(setting)

    def ratio_for_option(self, option: str) -> Ratio:
        """Return the ratio preset of an option, or raise if it doesn't exist."""
        if (setting := self._settings_by_option.get(option)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_ratio",
                translation_placeholders={"option": option},
            )
        return setting.ratio

    @override
    async def async_select_option(self, option: str) -> None:
        """Move the masks to a ratio preset.

        Moves to a preset start from the home position, so the masks are
        homed first when they are not already there.
        """
        ratio = self.ratio_for_option(option)
        client = self.runtime_data.client
        try:
            if not await self._async_at_home():
                await client.home(MotorID.ALL)
                await self._async_wait_until_stopped()
                # Homing can also end in a halt or an error, short of home
                if not await self._async_at_home():
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="homing_incomplete",
                    )
            await client.move_to_ratio(ratio)
        except SeymourError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.coordinator.async_request_refresh()

    async def _async_at_home(self) -> bool:
        """Return whether the masks are at rest in the home position.

        Asks the controller, since the polled data can be seconds old.
        """
        client = self.runtime_data.client
        status = await client.get_status()
        if status.code in MOVING_STATUS_CODES or status.code is StatusCode.ERROR:
            return False
        positions = {
            pos.motor_id: pos.position_pct for pos in await client.get_positions()
        }
        return is_at_home(
            positions, self.runtime_data.metadata.data.system_info.mask_ids
        )

    async def _async_wait_until_stopped(self) -> None:
        """Wait until the masks start moving, and then stop.

        Right after a command the controller can still report the masks at
        rest, so that only ends the wait once they were seen moving, or
        when they don't start within HOMING_START_TIMEOUT.
        """
        client = self.runtime_data.client
        loop = asyncio.get_running_loop()
        start_deadline = loop.time() + HOMING_START_TIMEOUT
        started = False
        try:
            async with asyncio.timeout(HOMING_TIMEOUT):
                while True:
                    if (await client.get_status()).code in MOVING_STATUS_CODES:
                        started = True
                    elif started or loop.time() >= start_deadline:
                        return
                    await asyncio.sleep(HOMING_POLL_INTERVAL)
        except TimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="homing_timeout",
            ) from err

    async def async_save_ratio(self, option: str) -> None:
        """Store the current mask positions in a ratio preset."""
        ratio = self.ratio_for_option(option)
        await self._async_update_ratio(self.runtime_data.client.update_ratio(ratio))

    async def async_reset_ratio(self, option: str) -> None:
        """Restore the factory positions of a ratio preset."""
        ratio = self.ratio_for_option(option)
        await self._async_update_ratio(
            self.runtime_data.client.reset_factory_default(ratio)
        )

    async def _async_update_ratio(self, command: Awaitable[None]) -> None:
        """Send a command that changes a preset, then refresh the presets."""
        try:
            await command
        except SeymourError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.runtime_data.metadata.async_request_refresh()
