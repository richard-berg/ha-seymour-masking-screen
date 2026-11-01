"""Sensor platform for the Seymour Masking Screen integration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import override

from seymourlib.protocol import StatusCode, SystemInfo

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SeymourConfigEntry, SeymourRuntimeData
from .entity import SeymourEntity, SeymourMetadataEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SeymourDimensionEntityDescription(SensorEntityDescription):
    """Describes a sensor for a dimension of the screen."""

    value_fn: Callable[[SystemInfo], float]


DIMENSIONS: tuple[SeymourDimensionEntityDescription, ...] = (
    SeymourDimensionEntityDescription(
        key="width",
        translation_key="width",
        value_fn=lambda system_info: system_info.width_inches,
    ),
    SeymourDimensionEntityDescription(
        key="height",
        translation_key="height",
        value_fn=lambda system_info: system_info.height_inches,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SeymourConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Seymour sensor entities."""
    runtime_data = entry.runtime_data
    async_add_entities(
        [
            SeymourStatusSensor(runtime_data),
            SeymourLastContactSensor(runtime_data),
            *(
                SeymourDimensionSensor(runtime_data, description)
                for description in DIMENSIONS
            ),
        ]
    )


class SeymourStatusSensor(SeymourEntity, SensorEntity):
    """What the masking screen is doing."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [code.name.lower() for code in StatusCode]
    _attr_translation_key = "status"

    def __init__(self, runtime_data: SeymourRuntimeData) -> None:
        """Initialize the sensor."""
        super().__init__(runtime_data, "status")

    @property
    @override
    def native_value(self) -> str:
        """Return the status of the controller."""
        return self.coordinator.data.status.code.name.lower()


class SeymourLastContactSensor(SeymourEntity, SensorEntity):
    """When the controller last answered.

    Unlike the other entities, this stays available while the controller is
    unreachable, so it shows since when.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "last_contact"

    def __init__(self, runtime_data: SeymourRuntimeData) -> None:
        """Initialize the sensor."""
        super().__init__(runtime_data, "last_contact")

    @property
    @override
    def available(self) -> bool:
        """Return True, so the sensor shows since when the screen is unreachable."""
        return True

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return when the last request succeeded."""
        last_success = self.runtime_data.client.connection_stats[
            "last_successful_operation"
        ]
        if last_success <= 0:
            return None
        return datetime.fromtimestamp(last_success, UTC)


class SeymourDimensionSensor(SeymourMetadataEntity, SensorEntity):
    """A dimension of the visible screen area."""

    entity_description: SeymourDimensionEntityDescription

    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfLength.INCHES

    def __init__(
        self,
        runtime_data: SeymourRuntimeData,
        description: SeymourDimensionEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(runtime_data, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> float:
        """Return the dimension."""
        return self.entity_description.value_fn(self.coordinator.data.system_info)
