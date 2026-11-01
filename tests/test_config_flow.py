"""Tests for the Seymour Masking Screen config flow."""

from unittest.mock import AsyncMock, patch

import attrs
import pytest
from pytest_homeassistant.common import MockConfigEntry
from seymourlib.discovery import DiscoveryError, TCPTransportCandidate
from seymourlib.exceptions import SeymourConnectionError, SeymourProtocolError
from seymourlib.protocol import decode_system_info
from seymourlib.transport import TCPTransport

from homeassistant.components.seymour_masking_screen.const import DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_DEVICE, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType

from . import MOCK_DEVICE, MOCK_IP2SL_HOST, MOCK_IP2SL_URL, SERIAL_NUMBER

DISCOVER = (
    "homeassistant.components.seymour_masking_screen.config_flow."
    "enumerate_tcp_transports"
)

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


async def _start_flow(hass: HomeAssistant, next_step_id: str) -> FlowResult:
    """Start a user flow and pick a menu option."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": next_step_id}
    )


async def _start_ip2sl_flow(hass: HomeAssistant) -> FlowResult:
    """Start a flow for an IP2SL and wait for discovery to finish."""
    result = await _start_flow(hass, "ip2sl")
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "ip2sl"
    assert result["description_placeholders"] == {"seconds": "12"}
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(result["flow_id"])


def _candidate(host: str, model: str | None = "iTachIP2SL") -> TCPTransportCandidate:
    """Return a discovered IP2SL."""
    return TCPTransportCandidate(
        transport=TCPTransport(host),
        host=host,
        port=4999,
        metadata={"Model": model} if model else {},
        raw_beacon="AMXB",
    )


async def test_serial_flow(hass: HomeAssistant, mock_seymour_client: AsyncMock) -> None:
    """Test setting up a controller on a serial port."""
    result = await _start_flow(hass, "serial")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "serial"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: MOCK_DEVICE}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Seymour PRH-123"
    assert result["data"] == {CONF_DEVICE: MOCK_DEVICE}
    assert result["result"].unique_id == SERIAL_NUMBER
    mock_seymour_client.connect.assert_awaited_once()
    mock_seymour_client.close.assert_awaited_once()


async def test_title_without_model(
    hass: HomeAssistant, mock_seymour_client: AsyncMock
) -> None:
    """Test the entry title when the controller reports no model."""
    mock_seymour_client.get_system_info.return_value = attrs.evolve(
        mock_seymour_client.get_system_info.return_value, screen_model=""
    )

    result = await _start_flow(hass, "serial")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: MOCK_DEVICE}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Seymour masking screen"
    assert result["result"].unique_id == SERIAL_NUMBER


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (SeymourConnectionError("timeout"), "cannot_connect"),
        (SeymourProtocolError("garbage"), "not_seymour"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_serial_flow_errors(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    side_effect: Exception,
    error: str,
) -> None:
    """Test errors while validating a serial port, and recovering from them."""
    system_info = mock_seymour_client.get_system_info.return_value
    mock_seymour_client.get_system_info.side_effect = side_effect

    result = await _start_flow(hass, "serial")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: MOCK_DEVICE}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    # The single-connection IP2SL must be released even when validation fails
    mock_seymour_client.close.assert_awaited_once()

    mock_seymour_client.get_system_info.side_effect = None
    mock_seymour_client.get_system_info.return_value = system_info
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: MOCK_DEVICE}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_serial_flow_port_already_configured(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test choosing a port that is already used by an entry."""
    mock_config_entry.add_to_hass(hass)

    result = await _start_flow(hass, "serial")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: MOCK_DEVICE}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_seymour_client.connect.assert_not_awaited()


async def test_controller_already_configured_updates_port(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test adding a configured controller on a new port updates the entry."""
    mock_config_entry.add_to_hass(hass)

    result = await _start_flow(hass, "serial")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: "/dev/ttyUSB1"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data == {CONF_DEVICE: "/dev/ttyUSB1"}


async def test_ip2sl_flow_discovered(
    hass: HomeAssistant, mock_seymour_client: AsyncMock
) -> None:
    """Test setting up a controller behind a discovered IP2SL."""
    with patch(
        DISCOVER,
        return_value=[_candidate(MOCK_IP2SL_HOST), _candidate("192.168.1.71", None)],
    ):
        result = await _start_ip2sl_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ip2sl_host"
    schema = result["data_schema"].schema
    host_key = next(key for key in schema if key == CONF_HOST)
    assert host_key.description == {"suggested_value": MOCK_IP2SL_HOST}
    assert [option["label"] for option in schema[host_key].config["options"]] == [
        "iTachIP2SL (192.168.1.70)",
        "IP2SL (192.168.1.71)",
    ]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: MOCK_IP2SL_HOST, CONF_PORT: 4999}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_DEVICE: MOCK_IP2SL_URL}
    assert result["result"].unique_id == SERIAL_NUMBER


@pytest.mark.parametrize("side_effect", [DiscoveryError("no multicast"), OSError])
async def test_ip2sl_flow_manual_host(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    side_effect: Exception,
) -> None:
    """Test entering an IP2SL address when discovery finds nothing."""
    with patch(DISCOVER, side_effect=side_effect):
        result = await _start_ip2sl_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ip2sl_host"

    mock_seymour_client.get_system_info.side_effect = SeymourConnectionError
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: " 192.168.1.70 ", CONF_PORT: 4998}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_seymour_client.get_system_info.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "ip2sl.local", CONF_PORT: 5000.0}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_DEVICE: "socket://ip2sl.local:5000"}


async def _start_reconfigure(
    hass: HomeAssistant, entry: MockConfigEntry, next_step_id: str
) -> FlowResult:
    """Start a reconfigure flow and pick a menu option."""
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "reconfigure"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": next_step_id}
    )


async def test_reconfigure_serial(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test moving a controller to another serial port."""
    mock_config_entry.add_to_hass(hass)

    result = await _start_reconfigure(hass, mock_config_entry, "serial")
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    device_key = next(key for key in schema if key == CONF_DEVICE)
    assert device_key.description == {"suggested_value": MOCK_DEVICE}

    # The current port of the entry itself doesn't count as already configured
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: MOCK_DEVICE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    result = await _start_reconfigure(hass, mock_config_entry, "serial")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: MOCK_IP2SL_URL}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == {CONF_DEVICE: MOCK_IP2SL_URL}


async def test_reconfigure_ip2sl(
    hass: HomeAssistant, mock_seymour_client: AsyncMock
) -> None:
    """Test moving a controller behind an IP2SL to another address."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "socket://[fe80::1]:5000"},
        unique_id=SERIAL_NUMBER,
    )
    entry.add_to_hass(hass)

    with patch(DISCOVER, return_value=[]):
        result = await _start_reconfigure(hass, entry, "ip2sl")
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    schema = result["data_schema"].schema
    suggested = {str(key): key.description for key in schema}
    assert suggested == {
        CONF_HOST: {"suggested_value": "fe80::1"},
        CONF_PORT: {"suggested_value": 5000},
    }

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: MOCK_IP2SL_HOST, CONF_PORT: 4999}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_DEVICE: MOCK_IP2SL_URL}


async def test_reconfigure_serial_not_prefilled_from_ip2sl(
    hass: HomeAssistant, mock_seymour_client: AsyncMock
) -> None:
    """Test an IP2SL address is not suggested as a serial port."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: MOCK_IP2SL_URL}, unique_id=SERIAL_NUMBER
    )
    entry.add_to_hass(hass)

    result = await _start_reconfigure(hass, entry, "serial")

    schema = result["data_schema"].schema
    device_key = next(key for key in schema if key == CONF_DEVICE)
    assert device_key.description is None


async def test_reconfigure_wrong_controller(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test reconfiguring to a different controller is refused."""
    mock_config_entry.add_to_hass(hass)
    mock_seymour_client.get_system_info.return_value = decode_system_info(
        b"[01PRH-123             0123.00069.1SS-0325-99999TBLR]"
    )

    result = await _start_reconfigure(hass, mock_config_entry, "serial")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: "/dev/ttyUSB1"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_controller"
    assert mock_config_entry.data == {CONF_DEVICE: MOCK_DEVICE}


@pytest.mark.parametrize(
    "url", ["socket://:4999", "socket://host:port", "socket://host"]
)
async def test_reconfigure_malformed_socket_url(
    hass: HomeAssistant, mock_seymour_client: AsyncMock, url: str
) -> None:
    """Test a malformed socket URL is suggested as a serial port instead."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE: url}, unique_id=SERIAL_NUMBER
    )
    entry.add_to_hass(hass)

    result = await _start_reconfigure(hass, entry, "serial")

    schema = result["data_schema"].schema
    device_key = next(key for key in schema if key == CONF_DEVICE)
    assert device_key.description == {"suggested_value": url}
