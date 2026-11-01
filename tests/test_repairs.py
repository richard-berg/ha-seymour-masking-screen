"""Tests for the Seymour Masking Screen repairs."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant.components.repairs import (
    process_repair_fix_flow,
    start_repair_fix_flow,
)
from pytest_homeassistant.typing import ClientSessionGenerator
from seymourlib.exceptions import (
    SeymourConnectionError,
    SeymourProtocolError,
    SeymourTimeoutError,
)
from seymourlib.itach import SEYMOUR_SERIAL_SETTINGS, SerialSettings

from homeassistant.components.repairs import DOMAIN as REPAIRS_DOMAIN
from homeassistant.components.seymour_masking_screen.const import DOMAIN
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from . import MOCK_IP2SL_HOST, MOCK_IP2SL_URL, SERIAL_NUMBER

GET_SETTINGS = (
    "homeassistant.components.seymour_masking_screen.coordinator.get_serial_settings"
)
SET_SETTINGS = (
    "homeassistant.components.seymour_masking_screen.repairs.set_serial_settings"
)
FACTORY_SETTINGS = SerialSettings(9600, "FLOW_NONE", "PARITY_NO")


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for a controller behind an IP2SL."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Seymour PRH-123",
        data={CONF_DEVICE: MOCK_IP2SL_URL},
        unique_id=SERIAL_NUMBER,
    )


async def _fail_polls(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    entry: MockConfigEntry,
    count: int,
) -> None:
    """Let the given number of polls happen."""
    for _ in range(count):
        interval = entry.runtime_data.status.update_interval
        assert interval is not None
        freezer.tick(interval)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def _issue_id(entry: MockConfigEntry) -> str:
    return f"ip2sl_serial_settings_{entry.entry_id}"


async def test_wrong_bridge_settings_are_fixed(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_seymour_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a silent controller leads to the IP2SL's settings, which get fixed."""
    assert await async_setup_component(hass, REPAIRS_DOMAIN, {})
    mock_seymour_client.get_status.side_effect = SeymourTimeoutError

    with patch(GET_SETTINGS, return_value=FACTORY_SETTINGS) as get_settings:
        await _fail_polls(hass, freezer, init_integration, 2)
        get_settings.assert_not_awaited()
        await _fail_polls(hass, freezer, init_integration, 3)
    # Checked once per outage
    get_settings.assert_awaited_once_with(MOCK_IP2SL_HOST)

    issue = issue_registry.async_get_issue(DOMAIN, _issue_id(init_integration))
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders == {
        "title": "Seymour PRH-123",
        "baudrate": "9600",
        "flow_control": "FLOW_NONE",
        "parity": "PARITY_NO",
    }

    client = await hass_client()
    data = await start_repair_fix_flow(client, DOMAIN, _issue_id(init_integration))
    assert data["step_id"] == "confirm"
    assert data["description_placeholders"] == issue.translation_placeholders

    mock_seymour_client.get_status.side_effect = None
    with patch(SET_SETTINGS, return_value=SEYMOUR_SERIAL_SETTINGS) as set_settings:
        data = await process_repair_fix_flow(client, data["flow_id"], json={})
    assert data["type"] == "create_entry"
    set_settings.assert_awaited_once_with(MOCK_IP2SL_HOST)
    assert issue_registry.async_get_issue(DOMAIN, _issue_id(init_integration)) is None
    assert init_integration.runtime_data.status.last_update_success


async def test_fix_fails(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_seymour_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the fix aborts when the IP2SL can't be changed."""
    assert await async_setup_component(hass, REPAIRS_DOMAIN, {})
    mock_seymour_client.get_status.side_effect = SeymourTimeoutError
    with patch(GET_SETTINGS, return_value=FACTORY_SETTINGS):
        await _fail_polls(hass, freezer, init_integration, 3)

    client = await hass_client()
    data = await start_repair_fix_flow(client, DOMAIN, _issue_id(init_integration))
    with patch(SET_SETTINGS, side_effect=SeymourConnectionError):
        data = await process_repair_fix_flow(client, data["flow_id"], json={})
    assert data["type"] == "abort"
    assert data["reason"] == "cannot_connect"


async def test_fix_after_unload(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_seymour_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a fix started before the entry unloaded still applies."""
    assert await async_setup_component(hass, REPAIRS_DOMAIN, {})
    mock_seymour_client.get_status.side_effect = SeymourTimeoutError
    with patch(GET_SETTINGS, return_value=FACTORY_SETTINGS):
        await _fail_polls(hass, freezer, init_integration, 3)
    client = await hass_client()
    data = await start_repair_fix_flow(client, DOMAIN, _issue_id(init_integration))

    await hass.config_entries.async_unload(init_integration.entry_id)
    with patch(SET_SETTINGS, return_value=SEYMOUR_SERIAL_SETTINGS) as set_settings:
        data = await process_repair_fix_flow(client, data["flow_id"], json={})
    assert data["type"] == "create_entry"
    set_settings.assert_awaited_once()


@pytest.mark.parametrize(
    ("settings", "side_effect"),
    [
        (SEYMOUR_SERIAL_SETTINGS, None),
        # Not an iTach, or it doesn't answer on its command port
        (None, SeymourConnectionError),
        (None, SeymourProtocolError),
    ],
)
async def test_bridge_settings_fine_or_unknown(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_seymour_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
    settings: SerialSettings | None,
    side_effect: type[Exception] | None,
) -> None:
    """Test no issue is raised when the settings are fine or can't be read."""
    mock_seymour_client.get_status.side_effect = SeymourTimeoutError
    with patch(GET_SETTINGS, return_value=settings, side_effect=side_effect):
        await _fail_polls(hass, freezer, init_integration, 3)
    assert issue_registry.async_get_issue(DOMAIN, _issue_id(init_integration)) is None


@pytest.mark.parametrize(
    ("device", "side_effect"),
    [
        # Not an IP2SL's own serial port
        ("socket://192.168.1.70:5000", SeymourTimeoutError),
        ("/dev/ttyUSB0", SeymourTimeoutError),
        # The bridge itself is unreachable
        (MOCK_IP2SL_URL, SeymourConnectionError),
    ],
)
async def test_bridge_settings_not_checked(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
    device: str,
    side_effect: type[Exception],
) -> None:
    """Test the settings are only checked for a silent controller on an IP2SL."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: device}, unique_id=SERIAL_NUMBER
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mock_seymour_client.get_status.side_effect = side_effect

    with patch(GET_SETTINGS) as get_settings:
        await _fail_polls(hass, freezer, entry, 3)
    get_settings.assert_not_awaited()


async def test_issue_cleared_when_controller_answers(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_seymour_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the issue goes away once the controller answers, however that came."""
    mock_seymour_client.get_status.side_effect = SeymourTimeoutError
    with patch(GET_SETTINGS, return_value=FACTORY_SETTINGS):
        await _fail_polls(hass, freezer, init_integration, 3)
    assert issue_registry.async_get_issue(DOMAIN, _issue_id(init_integration))

    mock_seymour_client.get_status.side_effect = None
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, _issue_id(init_integration)) is None
