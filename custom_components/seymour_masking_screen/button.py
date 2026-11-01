"""Button platform for the Seymour Masking Screen integration."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import override

from seymourlib.client import SeymourClient
from seymourlib.exceptions import SeymourError
from seymourlib.protocol import MotorID

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import SeymourConfigEntry, SeymourRuntimeData
from .entity import SeymourEntity
from .masks import mask_targets

# Requests are serialized by the client; holding commands back here would
# make a stop wait for every command before it.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SeymourButtonEntityDescription(ButtonEntityDescription):
    """Describes a Seymour button."""

    press_fn: Callable[[SeymourClient, MotorID], Awaitable[None]]


BUTTONS: tuple[SeymourButtonEntityDescription, ...] = (
    SeymourButtonEntityDescription(
        key="home",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda client, motor_id: client.home(motor_id),
    ),
    SeymourButtonEntityDescription(
        key="calibrate",
        entity_category=EntityCategory.CONFIG,
        # Calibrating takes long and is rarely needed; a stray press shouldn't
        entity_registry_enabled_default=False,
        press_fn=lambda client, motor_id: client.calibrate(motor_id),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SeymourConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Seymour button entities."""
    runtime_data = entry.runtime_data
    targets = mask_targets(runtime_data.metadata.data.system_info.mask_ids)
    async_add_entities(
        SeymourButton(
            runtime_data,
            description,
            motor_id,
            # Single masks are rarely maintained on their own when groups exist
            enabled_default=description.entity_registry_enabled_default
            and (len(targets) == 1 or members != [motor_id]),
        )
        for description in BUTTONS
        for motor_id, members in targets
    )


class SeymourButton(SeymourEntity, ButtonEntity):
    """A maintenance action for a mask or a group of masks."""

    entity_description: SeymourButtonEntityDescription

    def __init__(
        self,
        runtime_data: SeymourRuntimeData,
        description: SeymourButtonEntityDescription,
        motor_id: MotorID,
        *,
        enabled_default: bool,
    ) -> None:
        """Initialize the button."""
        key = f"{description.key}_{motor_id.name.lower()}"
        super().__init__(runtime_data, key)
        self.entity_description = description
        self._attr_translation_key = key
        self._motor_id = motor_id
        self._attr_entity_registry_enabled_default = enabled_default

    @override
    async def async_press(self) -> None:
        """Send the action to the controller."""
        # The newest command wins over a move to a ratio preset in progress
        self.runtime_data.cancel_preset_move()
        try:
            await self.entity_description.press_fn(
                self.runtime_data.client, self._motor_id
            )
        except SeymourError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self.coordinator.async_expect_motion()
        await self.coordinator.async_request_refresh()
