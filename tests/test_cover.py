"""Tests for the Seymour Masking Screen cover platform."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from seymourlib.exceptions import SeymourConnectionError
from seymourlib.protocol import MotorID, MovementCode, decode_positions, decode_status
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.cover import (
    DOMAIN as COVER_DOMAIN,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_STOP_COVER,
    CoverState,
)
from homeassistant.components.seymour_masking_screen.const import DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

ALL_MASKS = "cover.seymour_prh_123_all_masks"
TOP_MASK = "cover.seymour_prh_123_top_mask"
VERTICAL_MASKS = "cover.seymour_prh_123_vertical_masks"
HORIZONTAL_MASKS = "cover.seymour_prh_123_horizontal_masks"


@pytest.fixture
def platforms() -> list[Platform]:
    """Only set up the cover platform."""
    return [Platform.COVER]


@pytest.mark.usefixtures("init_integration")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the cover entities."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.parametrize(
    ("system_info_frame", "entity_ids"),
    [
        (
            b"[01PRH-123             0123.00069.1SS-0325-12345TB]",
            [
                ALL_MASKS,
                "cover.seymour_prh_123_bottom_mask",
                TOP_MASK,
            ],
        ),
        (b"[01PRH-123             0123.00069.1SS-0325-12345T]", [TOP_MASK]),
        (
            b"[01PRH-123             0123.00069.1SS-0325-12345TBL]",
            [
                ALL_MASKS,
                "cover.seymour_prh_123_bottom_mask",
                "cover.seymour_prh_123_left_mask",
                TOP_MASK,
                VERTICAL_MASKS,
            ],
        ),
        (
            b"[01PRH-123             0123.00069.1SS-0325-12345TBLR]",
            [
                ALL_MASKS,
                "cover.seymour_prh_123_bottom_mask",
                HORIZONTAL_MASKS,
                "cover.seymour_prh_123_left_mask",
                "cover.seymour_prh_123_right_mask",
                TOP_MASK,
                VERTICAL_MASKS,
            ],
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_entities_follow_mask_count(
    hass: HomeAssistant, entity_ids: list[str]
) -> None:
    """Test the group covers created for each combination of masks."""
    assert sorted(hass.states.async_entity_ids(COVER_DOMAIN)) == entity_ids


@pytest.mark.parametrize(
    ("service", "method", "args"),
    [
        (SERVICE_OPEN_COVER, "move_out", (MotorID.ALL, MovementCode.UNTIL_LIMIT)),
        (SERVICE_CLOSE_COVER, "move_in", (MotorID.ALL, MovementCode.UNTIL_LIMIT)),
        (SERVICE_STOP_COVER, "halt", (MotorID.ALL,)),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_cover_actions(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    service: str,
    method: str,
    args: tuple[object, ...],
) -> None:
    """Test the cover actions send the matching command and refresh."""
    mock_seymour_client.get_status.reset_mock()

    await hass.services.async_call(
        COVER_DOMAIN, service, {ATTR_ENTITY_ID: ALL_MASKS}, blocking=True
    )

    getattr(mock_seymour_client, method).assert_awaited_once_with(*args)
    mock_seymour_client.get_status.assert_awaited_once()


@pytest.mark.usefixtures("init_integration")
async def test_cover_action_single_mask(
    hass: HomeAssistant, mock_seymour_client: AsyncMock
) -> None:
    """Test a single mask cover only moves its own motor."""
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: TOP_MASK}, blocking=True
    )

    mock_seymour_client.move_out.assert_awaited_once_with(
        MotorID.TOP, MovementCode.UNTIL_LIMIT
    )


@pytest.mark.parametrize(
    ("entity_id", "motor_id"),
    [(VERTICAL_MASKS, MotorID.VERTICAL), (HORIZONTAL_MASKS, MotorID.HORIZONTAL)],
)
@pytest.mark.usefixtures("init_integration")
async def test_cover_action_mask_pair(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    entity_id: str,
    motor_id: MotorID,
) -> None:
    """Test a mask pair cover moves both masks with the pair's motor ID."""
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_CLOSE_COVER, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    mock_seymour_client.move_in.assert_awaited_once_with(
        motor_id, MovementCode.UNTIL_LIMIT
    )


@pytest.mark.usefixtures("init_integration")
async def test_mask_pair_position(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a mask pair reports the average position of its two masks."""
    mock_seymour_client.get_positions.return_value = decode_positions(
        b"[014T40.0B60.0L99.0R99.0]"
    )
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    vertical = hass.states.get(VERTICAL_MASKS)
    assert vertical is not None
    assert vertical.state == CoverState.OPEN
    assert vertical.attributes["current_position"] == 50

    horizontal = hass.states.get(HORIZONTAL_MASKS)
    assert horizontal is not None
    assert horizontal.state == CoverState.CLOSED
    assert horizontal.attributes["current_position"] == 1


@pytest.mark.usefixtures("init_integration")
async def test_cover_action_failure(
    hass: HomeAssistant, mock_seymour_client: AsyncMock
) -> None:
    """Test a failed command raises a translated error."""
    mock_seymour_client.halt.side_effect = SeymourConnectionError("timeout")

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            COVER_DOMAIN, SERVICE_STOP_COVER, {ATTR_ENTITY_ID: ALL_MASKS}, blocking=True
        )

    assert exc_info.value.translation_key == "command_failed"


@pytest.mark.parametrize(
    ("status_frame", "positions_frame", "state", "position"),
    [
        (b"[01O]", b"[014T50.0B50.0L10.0R10.0]", CoverState.OPENING, 70),
        (b"[01I]", b"[014T50.0B50.0L10.0R10.0]", CoverState.CLOSING, 70),
        (b"[01H]", b"[014T99.0B99.0L99.0R99.0]", CoverState.CLOSED, 1),
        (b"[01P178]", b"[014T00.0B00.0L00.0R00.0]", CoverState.OPEN, 100),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_cover_state(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
    status_frame: bytes,
    positions_frame: bytes,
    state: CoverState,
    position: int,
) -> None:
    """Test the cover state follows the controller status and positions."""
    mock_seymour_client.get_status.return_value = decode_status(status_frame)
    mock_seymour_client.get_positions.return_value = decode_positions(positions_frame)

    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    cover = hass.states.get(ALL_MASKS)
    assert cover is not None
    assert cover.state == state
    assert cover.attributes["current_position"] == position


@pytest.mark.usefixtures("init_integration")
async def test_polls_faster_while_moving(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the status is polled every second while the masks move."""
    mock_seymour_client.get_status.return_value = decode_status(b"[01M178]")
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    mock_seymour_client.get_status.reset_mock()

    freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    mock_seymour_client.get_status.assert_awaited_once()

    mock_seymour_client.get_status.return_value = decode_status(b"[01P178]")
    freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    mock_seymour_client.get_status.reset_mock()

    freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    mock_seymour_client.get_status.assert_not_awaited()


@pytest.mark.usefixtures("init_integration")
async def test_unavailable_when_update_fails(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the covers become unavailable when polling fails, then recover."""
    mock_seymour_client.get_status.side_effect = SeymourConnectionError("timeout")
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    cover = hass.states.get(ALL_MASKS)
    assert cover is not None
    assert cover.state == STATE_UNAVAILABLE

    mock_seymour_client.get_status.side_effect = None
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    cover = hass.states.get(ALL_MASKS)
    assert cover is not None
    assert cover.state == CoverState.OPEN


@pytest.mark.usefixtures("init_integration")
async def test_cover_without_position(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a mask without a reported position has an unknown state."""
    mock_seymour_client.get_positions.return_value = decode_positions(b"[011T10.0]")
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    cover = hass.states.get("cover.seymour_prh_123_bottom_mask")
    assert cover is not None
    assert cover.state == STATE_UNKNOWN
    assert "current_position" not in cover.attributes


@pytest.mark.parametrize(
    ("direction", "distance", "method", "movement"),
    [
        ("open", "jog", "move_out", MovementCode.JOG),
        ("close", "percent", "move_in", MovementCode.MOVE),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_move_mask(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
    direction: str,
    distance: str,
    method: str,
    movement: MovementCode,
) -> None:
    """Test the move mask action moves the masks a small step."""
    await hass.services.async_call(
        DOMAIN,
        "move_mask",
        {ATTR_ENTITY_ID: VERTICAL_MASKS, "direction": direction, "distance": distance},
        blocking=True,
    )

    getattr(mock_seymour_client, method).assert_awaited_once_with(
        MotorID.VERTICAL, movement
    )


@pytest.mark.usefixtures("init_integration")
async def test_stop_does_not_wait_for_other_commands(
    hass: HomeAssistant,
    mock_seymour_client: AsyncMock,
) -> None:
    """Test a stop goes out while another cover's command is still running."""
    release = asyncio.Event()

    async def slow_move_out(*args: object) -> None:
        await release.wait()

    mock_seymour_client.move_out.side_effect = slow_move_out
    opening = hass.async_create_task(
        hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_OPEN_COVER,
            {ATTR_ENTITY_ID: ALL_MASKS},
            blocking=True,
        )
    )
    await asyncio.sleep(0)

    async with asyncio.timeout(1):
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_STOP_COVER,
            {ATTR_ENTITY_ID: TOP_MASK},
            blocking=True,
        )
    mock_seymour_client.halt.assert_awaited_once_with(MotorID.TOP)

    release.set()
    await opening
