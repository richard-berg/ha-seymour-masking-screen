"""Base entities for the Seymour Masking Screen integration."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import (
    SeymourMetadataCoordinator,
    SeymourRuntimeData,
    SeymourStatusCoordinator,
)


def _unique_prefix(runtime_data: SeymourRuntimeData) -> str:
    """Return the prefix of the unique IDs of the screen's entities.

    That is the serial number the entry was set up with, not the one the
    controller reports now: a replaced controller keeps the entities, and the
    automations that use them. A controller without a serial number uses the
    entry ID.
    """
    entry = runtime_data.metadata.config_entry
    return entry.unique_id or entry.entry_id


def _device_info(runtime_data: SeymourRuntimeData, unique_prefix: str) -> DeviceInfo:
    """Return the device info of the masking screen."""
    system_info = runtime_data.metadata.data.system_info
    model = system_info.screen_model or None
    return DeviceInfo(
        identifiers={(DOMAIN, unique_prefix)},
        manufacturer=MANUFACTURER,
        model=model,
        name=f"Seymour {model}" if model else "Seymour masking screen",
        serial_number=system_info.serial_number.to_serial_number() or None,
    )


class SeymourEntity(CoordinatorEntity[SeymourStatusCoordinator]):
    """Base entity that follows the status of a Seymour masking screen."""

    _attr_has_entity_name = True

    def __init__(self, runtime_data: SeymourRuntimeData, key: str) -> None:
        """Initialize the entity."""
        super().__init__(runtime_data.status)
        self.runtime_data = runtime_data
        unique_prefix = _unique_prefix(runtime_data)
        self._attr_unique_id = f"{unique_prefix}_{key}"
        self._attr_device_info = _device_info(runtime_data, unique_prefix)


class SeymourMetadataEntity(CoordinatorEntity[SeymourMetadataCoordinator]):
    """Base entity that follows the metadata of a Seymour masking screen."""

    _attr_has_entity_name = True

    def __init__(self, runtime_data: SeymourRuntimeData, key: str) -> None:
        """Initialize the entity."""
        super().__init__(runtime_data.metadata)
        self.runtime_data = runtime_data
        unique_prefix = _unique_prefix(runtime_data)
        self._attr_unique_id = f"{unique_prefix}_{key}"
        self._attr_device_info = _device_info(runtime_data, unique_prefix)
