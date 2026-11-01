"""Tests for the Seymour Masking Screen integration setup."""

from datetime import timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
from seymourlib.exceptions import SeymourConnectionError, SeymourProtocolError
from seymourlib.protocol import decode_system_info
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.seymour_masking_screen.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from homeassistant.helpers import device_registry as dr, issue_registry as ir

from . import MOCK_DEVICE, SERIAL_NUMBER


async def test_setup_and_unload(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_seymour_client: AsyncMock,
) -> None:
    """Test setting up and unloading the integration."""
    assert init_integration.state is ConfigEntryState.LOADED
    mock_seymour_client.close.assert_not_awaited()

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.NOT_LOADED
    mock_seymour_client.close.assert_awaited_once()


async def test_device_info(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test the device created for the controller."""
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, SERIAL_NUMBER), init_integration.entry_id
    )
    assert device == snapshot


@pytest.mark.parametrize(
    "method", ["connect", "get_system_info", "get_ratio_settings", "get_status"]
)
async def test_setup_retry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_seymour_client: AsyncMock,
    method: str,
) -> None:
    """Test setup is retried, and the connection released, when it fails."""
    getattr(mock_seymour_client, method).side_effect = SeymourConnectionError
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_seymour_client.close.assert_awaited_once()


async def test_setup_retry_hides_api_key(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test the setup retry message does not show an ESPHome API key."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "esphome://proxy.local:6053/?port_name=RS232&key=SECRET"},
        unique_id=SERIAL_NUMBER,
    )
    entry.add_to_hass(hass)
    mock_seymour_client.connect.side_effect = SeymourConnectionError

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert "esphome://proxy.local:6053/" in caplog.text
    assert "SECRET" not in caplog.text


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Let the status coordinator poll."""
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_unreachable_issue(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_seymour_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a repair issue is raised while the controller is unreachable."""
    issue_id = f"unreachable_{init_integration.entry_id}"
    mock_seymour_client.get_status.side_effect = SeymourConnectionError

    for _ in range(2):
        await _poll(hass, freezer)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None

    await _poll(hass, freezer)
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"title": "Seymour PRH-123"}

    mock_seymour_client.get_status.side_effect = None
    await _poll(hass, freezer)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_unreachable_issue_removed_on_unload(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_seymour_client: AsyncMock,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the repair issue goes away with the config entry."""
    mock_seymour_client.get_status.side_effect = SeymourConnectionError
    for _ in range(3):
        await _poll(hass, freezer)
    assert len(issue_registry.issues) == 1

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert len(issue_registry.issues) == 0


@pytest.mark.parametrize(
    ("side_effect", "closes"),
    [(SeymourConnectionError, 1), (SeymourProtocolError, 0)],
)
async def test_connection_reset_after_failures(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_seymour_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
    side_effect: type[Exception],
    closes: int,
) -> None:
    """Test the connection is reset after repeated connection failures."""
    mock_seymour_client.get_status.side_effect = side_effect

    for _ in range(4):
        await _poll(hass, freezer)
    mock_seymour_client.disconnect.assert_not_awaited()

    await _poll(hass, freezer)
    assert mock_seymour_client.disconnect.await_count == closes
    # The client must stay usable: close() would end it for good
    mock_seymour_client.close.assert_not_awaited()


async def _start_reconfigure(hass: HomeAssistant, entry: MockConfigEntry) -> FlowResult:
    """Start reconfiguring an entry, for a controller on a serial port."""
    result = await entry.start_reconfigure_flow(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "serial"}
    )


async def test_reconfigure_releases_loaded_entry(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    init_integration: MockConfigEntry,
) -> None:
    """Test the loaded entry lets go of the controller while it is validated."""

    mock_seymour_client.reset_mock()

    result = await _start_reconfigure(hass, init_integration)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: "/dev/serial/by-id/usb-FTDI-if00"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    # Unloading the entry closed its connection before the validation opened one
    commands = [name for name, _, _ in mock_seymour_client.mock_calls]
    assert commands[:3] == ["close", "connect", "get_system_info"]
    assert init_integration.state is ConfigEntryState.LOADED
    assert init_integration.data == {CONF_DEVICE: "/dev/serial/by-id/usb-FTDI-if00"}


@pytest.mark.parametrize(
    ("validation", "reason"),
    [
        (
            decode_system_info(
                b"[01PRH-123             0123.00069.1SS-0325-99999TBLR]"
            ),
            "wrong_controller",
        ),
        (SeymourConnectionError(), None),
    ],
)
async def test_reconfigure_restores_loaded_entry(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    init_integration: MockConfigEntry,
    validation: object,
    reason: str | None,
) -> None:
    """Test the entry is loaded again when reconfiguring doesn't succeed."""
    original = mock_seymour_client.get_system_info.return_value
    # The new port answers with the validation, then the entry reloads
    mock_seymour_client.get_system_info.side_effect = [validation, original]

    result = await _start_reconfigure(hass, init_integration)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: "/dev/ttyUSB1"}
    )
    await hass.async_block_till_done()

    if reason:
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == reason
    else:
        assert result["errors"] == {"base": "cannot_connect"}
    assert init_integration.state is ConfigEntryState.LOADED
    assert init_integration.data == {CONF_DEVICE: MOCK_DEVICE}
