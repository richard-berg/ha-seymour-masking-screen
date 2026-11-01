"""Base entities for the Seymour Masking Screen integration."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import (
    SeymourMetadataCoordinator,
    SeymourRuntimeData,
    SeymourStatusCoordinator,
)


def _device_info(runtime_data: SeymourRuntimeData, serial_number: str) -> DeviceInfo:
    """Return the device info of the masking screen."""
    model = runtime_data.metadata.data.system_info.screen_model or None
    return DeviceInfo(
        identifiers={(DOMAIN, serial_number)},
        manufacturer=MANUFACTURER,
        model=model,
        name=f"Seymour {model}" if model else "Seymour masking screen",
        serial_number=serial_number,
    )


class SeymourEntity(CoordinatorEntity[SeymourStatusCoordinator]):
    """Base entity that follows the status of a Seymour masking screen."""

    _attr_has_entity_name = True

    def __init__(self, runtime_data: SeymourRuntimeData, key: str) -> None:
        """Initialize the entity."""
        super().__init__(runtime_data.status)
        self.runtime_data = runtime_data
        serial_number = runtime_data.metadata.data.system_info.serial_number
        unique_prefix = serial_number.to_serial_number()
        self._attr_unique_id = f"{unique_prefix}_{key}"
        self._attr_device_info = _device_info(runtime_data, unique_prefix)


class SeymourMetadataEntity(CoordinatorEntity[SeymourMetadataCoordinator]):
    """Base entity that follows the metadata of a Seymour masking screen."""

    _attr_has_entity_name = True

    def __init__(self, runtime_data: SeymourRuntimeData, key: str) -> None:
        """Initialize the entity."""
        super().__init__(runtime_data.metadata)
        self.runtime_data = runtime_data
        serial_number = runtime_data.metadata.data.system_info.serial_number
        unique_prefix = serial_number.to_serial_number()
        self._attr_unique_id = f"{unique_prefix}_{key}"
        self._attr_device_info = _device_info(runtime_data, unique_prefix)
