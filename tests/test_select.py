"""Tests for the Seymour Masking Screen select platform."""

from datetime import timedelta
from unittest.mock import AsyncMock, call, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from seymourlib.exceptions import SeymourConnectionError
from seymourlib.protocol import (
    MaskPosition,
    MaskStatus,
    MotorID,
    Ratio,
    StatusCode,
    decode_positions,
    decode_status,
)
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.seymour_masking_screen.const import DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

ASPECT_RATIO = "select.seymour_prh_123_aspect_ratio"
HOME_POSITIONS = b"[014T00.0B00.0L00.0R00.0]"


@pytest.fixture
def platforms() -> list[Platform]:
    """Only set up the select platform."""
    return [Platform.SELECT]


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Let the status coordinator poll."""
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("init_integration")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the select entities."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.parametrize(
    ("status_frame", "expected"),
    [
        (b"[01P235]", "2.35:1 (235)"),
        (b"[01M240]", "2.40 (240)"),
        (b"[01P990]", STATE_UNKNOWN),
        (b"[01P999]", STATE_UNKNOWN),
        (b"[01H]", STATE_UNKNOWN),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_current_option(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
    status_frame: bytes,
    expected: str,
) -> None:
    """Test the current option follows the preset the controller reports."""
    mock_seymour_client.get_status.return_value = decode_status(status_frame)
    await _poll(hass, freezer)

    state = hass.states.get(ASPECT_RATIO)
    assert state is not None
    assert state.state == expected


def _commands(client: AsyncMock) -> list[str]:
    """Return the names of the client methods called, in order."""
    return [name for name, _, _ in client.mock_calls]


async def _select(hass: HomeAssistant, option: str = "2.35:1 (235)") -> None:
    """Select a ratio preset."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: ASPECT_RATIO, ATTR_OPTION: option},
        blocking=True,
    )


@pytest.mark.usefixtures("init_integration")
async def test_select_ratio_at_home(
    hass: HomeAssistant, mock_seymour_client: AsyncMock
) -> None:
    """Test selecting a preset from the home position moves straight there."""
    mock_seymour_client.get_positions.return_value = decode_positions(HOME_POSITIONS)
    mock_seymour_client.reset_mock()

    await _select(hass)

    assert _commands(mock_seymour_client)[:3] == [
        "get_status",
        "get_positions",
        "move_to_ratio",
    ]
    mock_seymour_client.home.assert_not_awaited()
    mock_seymour_client.move_to_ratio.assert_awaited_once_with(Ratio("235"))


@pytest.mark.parametrize(
    "status_frame",
    [
        # Away from home and at rest
        b"[01H]",
        # Still moving, or in error, even with positions that read as home
        b"[01M240]",
        b"[01E]",
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_select_ratio_homes_first(
    hass: HomeAssistant, mock_seymour_client: AsyncMock, status_frame: bytes
) -> None:
    """Test the masks are homed, and the homing waited for, before the move."""
    away = decode_positions(
        HOME_POSITIONS if status_frame != b"[01H]" else b"[014T25.0B25.0L10.0R10.0]"
    )
    mock_seymour_client.get_status.side_effect = [
        decode_status(status_frame),
        decode_status(b"[01A]"),
        decode_status(b"[01A]"),
        decode_status(b"[01H]"),
        decode_status(b"[01H]"),
        decode_status(b"[01M235]"),
    ]
    mock_seymour_client.get_positions.side_effect = [
        away,
        decode_positions(HOME_POSITIONS),
        decode_positions(HOME_POSITIONS),
    ]
    mock_seymour_client.reset_mock()

    with patch(
        "homeassistant.components.seymour_masking_screen.select.HOMING_POLL_INTERVAL",
        0,
    ):
        await _select(hass)

    commands = _commands(mock_seymour_client)
    home = commands.index("home")
    move = commands.index("move_to_ratio")
    # Wait until homing ends, then check the masks really are home
    assert commands[home:move] == [
        "home",
        "get_status",
        "get_status",
        "get_status",
        "get_status",
        "get_positions",
    ]
    mock_seymour_client.home.assert_awaited_once_with(MotorID.ALL)
    mock_seymour_client.move_to_ratio.assert_awaited_once_with(Ratio("235"))


@pytest.mark.usefixtures("init_integration")
async def test_select_ratio_waits_for_homing_to_start(
    hass: HomeAssistant, mock_seymour_client: AsyncMock
) -> None:
    """Test a status at rest right after homing starts isn't taken as its end."""
    statuses = iter(
        [
            b"[01H]",
            # The controller hasn't reported the homing yet
            b"[01H]",
            b"[01H]",
            b"[01A]",
            b"[01H]",
            b"[01H]",
            b"[01M235]",
        ]
    )
    homed = False

    async def get_status() -> MaskStatus:
        nonlocal homed
        status = decode_status(next(statuses))
        homed |= status.code is StatusCode.HOMING
        return status

    async def get_positions() -> list[MaskPosition]:
        # The masks only reach home after the controller reported homing
        return decode_positions(
            HOME_POSITIONS if homed else b"[014T25.0B25.0L10.0R10.0]"
        )

    mock_seymour_client.get_status.side_effect = get_status
    mock_seymour_client.get_positions.side_effect = get_positions

    with patch(
        "homeassistant.components.seymour_masking_screen.select.HOMING_POLL_INTERVAL",
        0,
    ):
        await _select(hass)

    mock_seymour_client.move_to_ratio.assert_awaited_once_with(Ratio("235"))


@pytest.mark.parametrize("final_status", [b"[01H]", b"[01E]"])
@pytest.mark.usefixtures("init_integration")
async def test_select_ratio_homing_incomplete(
    hass: HomeAssistant, mock_seymour_client: AsyncMock, final_status: bytes
) -> None:
    """Test no preset move follows a homing that stopped short of home."""
    mock_seymour_client.get_status.side_effect = [
        decode_status(b"[01H]"),
        decode_status(final_status),
        decode_status(final_status),
    ]

    with (
        patch(
            "homeassistant.components.seymour_masking_screen.select.HOMING_START_TIMEOUT",
            0,
        ),
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await _select(hass)

    assert exc_info.value.translation_key == "homing_incomplete"
    mock_seymour_client.move_to_ratio.assert_not_awaited()


@pytest.mark.usefixtures("init_integration")
async def test_select_ratio_homing_timeout(
    hass: HomeAssistant, mock_seymour_client: AsyncMock
) -> None:
    """Test the preset move is abandoned when homing never finishes."""
    mock_seymour_client.get_status.return_value = decode_status(b"[01A]")

    with (
        patch(
            "homeassistant.components.seymour_masking_screen.select.HOMING_TIMEOUT",
            0.01,
        ),
        patch(
            "homeassistant.components.seymour_masking_screen.select.HOMING_POLL_INTERVAL",
            0,
        ),
        pytest.raises(HomeAssistantError) as exc_info,
    ):
        await _select(hass)

    assert exc_info.value.translation_key == "homing_timeout"
    mock_seymour_client.move_to_ratio.assert_not_awaited()


@pytest.mark.usefixtures("init_integration")
async def test_select_ratio_failure(
    hass: HomeAssistant, mock_seymour_client: AsyncMock
) -> None:
    """Test a failed preset move raises a translated error."""
    mock_seymour_client.get_positions.return_value = decode_positions(HOME_POSITIONS)
    mock_seymour_client.move_to_ratio.side_effect = SeymourConnectionError

    with pytest.raises(HomeAssistantError) as exc_info:
        await _select(hass)

    assert exc_info.value.translation_key == "command_failed"


@pytest.mark.parametrize(
    ("service", "method"),
    [("save_ratio", "update_ratio"), ("reset_ratio", "reset_factory_default")],
)
@pytest.mark.usefixtures("init_integration")
async def test_ratio_actions(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    service: str,
    method: str,
) -> None:
    """Test saving and resetting a preset, which refreshes the presets."""
    mock_seymour_client.get_ratio_settings.reset_mock()

    await hass.services.async_call(
        DOMAIN,
        service,
        {ATTR_ENTITY_ID: ASPECT_RATIO, ATTR_OPTION: "16:9 (178)"},
        blocking=True,
    )

    assert getattr(mock_seymour_client, method).await_args_list == [call(Ratio("178"))]
    mock_seymour_client.get_ratio_settings.assert_awaited_once()


@pytest.mark.usefixtures("init_integration")
async def test_ratio_action_failure(
    hass: HomeAssistant, mock_seymour_client: AsyncMock
) -> None:
    """Test a failed preset change raises a translated error."""
    mock_seymour_client.update_ratio.side_effect = SeymourConnectionError

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "save_ratio",
            {ATTR_ENTITY_ID: ASPECT_RATIO, ATTR_OPTION: "16:9 (178)"},
            blocking=True,
        )

    assert exc_info.value.translation_key == "command_failed"


@pytest.mark.parametrize(
    ("domain", "service", "translation_key"),
    [
        # Home Assistant itself refuses options the select doesn't offer
        (SELECT_DOMAIN, SERVICE_SELECT_OPTION, "not_valid_option"),
        (DOMAIN, "save_ratio", "unknown_ratio"),
        (DOMAIN, "reset_ratio", "unknown_ratio"),
    ],
)
@pytest.mark.parametrize("option", ["custom 0 (990)", "4:3 (133)"])
@pytest.mark.usefixtures("init_integration")
async def test_unknown_ratio(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    domain: str,
    service: str,
    translation_key: str,
    option: str,
) -> None:
    """Test unknown or unused presets are refused."""
    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            domain,
            service,
            {ATTR_ENTITY_ID: ASPECT_RATIO, ATTR_OPTION: option},
            blocking=True,
        )

    assert exc_info.value.translation_key == translation_key
    mock_seymour_client.move_to_ratio.assert_not_awaited()
    mock_seymour_client.update_ratio.assert_not_awaited()
    mock_seymour_client.reset_factory_default.assert_not_awaited()
