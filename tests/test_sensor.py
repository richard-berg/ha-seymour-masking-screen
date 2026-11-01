"""Tests for the Seymour Masking Screen sensor platform."""

from datetime import timedelta
import time
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from seymourlib.exceptions import SeymourConnectionError
from seymourlib.protocol import decode_status
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

STATUS = "sensor.seymour_prh_123_status"
LAST_CONTACT = "sensor.seymour_prh_123_last_contact"


@pytest.fixture
def platforms() -> list[Platform]:
    """Only set up the sensor platform."""
    return [Platform.SENSOR]


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Let the status coordinator poll."""
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the sensor entities."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_status(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the status sensor follows the controller."""
    mock_seymour_client.get_status.return_value = decode_status(b"[01M235]")
    await _poll(hass, freezer)

    state = hass.states.get(STATUS)
    assert state is not None
    assert state.state == "moving_to_ratio"


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "init_integration")
async def test_last_contact_while_unreachable(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the last contact sensor shows since when the controller is unreachable."""
    freezer.move_to("2026-11-01T12:00:00+00:00")
    mock_seymour_client.connection_stats = {
        "last_successful_operation": time.time(),
        "time_since_last_success": 0.0,
    }
    mock_seymour_client.get_status.side_effect = SeymourConnectionError
    await _poll(hass, freezer)

    status = hass.states.get(STATUS)
    assert status is not None
    assert status.state == STATE_UNAVAILABLE
    last_contact = hass.states.get(LAST_CONTACT)
    assert last_contact is not None
    assert last_contact.state == "2026-11-01T12:00:00+00:00"
