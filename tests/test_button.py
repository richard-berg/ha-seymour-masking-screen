"""Tests for the Seymour Masking Screen button platform."""

from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform
from seymourlib.exceptions import SeymourConnectionError
from seymourlib.protocol import MotorID
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er


@pytest.fixture
def platforms() -> list[Platform]:
    """Only set up the button platform."""
    return [Platform.BUTTON]


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the button entities."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.parametrize(
    ("entity_id", "enabled"),
    [
        ("button.seymour_prh_123_home_all_masks", True),
        ("button.seymour_prh_123_home_top_mask", False),
        ("button.seymour_prh_123_calibrate_all_masks", False),
        ("button.seymour_prh_123_calibrate_vertical_masks", False),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_enabled_by_default(
    entity_registry: er.EntityRegistry, entity_id: str, enabled: bool
) -> None:
    """Test single mask buttons, and calibrate buttons, are disabled."""
    entry = entity_registry.async_get(entity_id)
    assert entry is not None
    assert (entry.disabled_by is None) is enabled


@pytest.mark.parametrize(
    "system_info_frame", [b"[01PRH-123             0123.00069.1SS-0325-12345T]"]
)
@pytest.mark.usefixtures("init_integration")
async def test_enabled_single_mask_screen(entity_registry: er.EntityRegistry) -> None:
    """Test the home button of a screen with a single mask is enabled."""
    entry = entity_registry.async_get("button.seymour_prh_123_home_top_mask")
    assert entry is not None
    assert entry.disabled_by is None
    entry = entity_registry.async_get("button.seymour_prh_123_calibrate_top_mask")
    assert entry is not None
    assert entry.disabled_by is not None


@pytest.mark.parametrize(
    ("entity_id", "method", "motor_id"),
    [
        ("button.seymour_prh_123_home_all_masks", "home", MotorID.ALL),
        (
            "button.seymour_prh_123_calibrate_horizontal_masks",
            "calibrate",
            MotorID.HORIZONTAL,
        ),
    ],
)
@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_press(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    entity_id: str,
    method: str,
    motor_id: MotorID,
) -> None:
    """Test pressing a button sends the action for its masks."""
    mock_seymour_client.get_status.reset_mock()

    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    getattr(mock_seymour_client, method).assert_awaited_once_with(motor_id)
    mock_seymour_client.get_status.assert_awaited_once()


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_press_failure(
    hass: HomeAssistant, mock_seymour_client: AsyncMock
) -> None:
    """Test a failed action raises a translated error."""
    mock_seymour_client.home.side_effect = SeymourConnectionError

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            BUTTON_DOMAIN,
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: "button.seymour_prh_123_home_all_masks"},
            blocking=True,
        )

    assert exc_info.value.translation_key == "command_failed"
