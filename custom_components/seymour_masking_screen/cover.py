"""Cover platform for the Seymour Masking Screen integration.

Each mask, pair of masks, and all masks together are exposed as covers; see
`mask_targets`.
A cover is open when its masks are retracted and closed when they are fully
extended; a group reports the average position of its masks.
"""

from collections.abc import Awaitable
from enum import StrEnum
from typing import Any, override

from seymourlib.exceptions import SeymourError
from seymourlib.protocol import MotorID, MovementCode, StatusCode

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import MOVING_STATUS_CODES, SeymourConfigEntry, SeymourRuntimeData
from .entity import SeymourEntity
from .masks import HOME_TOLERANCE, mask_targets

# Requests are serialized by the client; holding commands back here would
# make a stop wait for every command before it.
PARALLEL_UPDATES = 0

# A mask within this many percent of full extension counts as closed, which
# absorbs small encoder offsets.
CLOSED_TOLERANCE = 2.0
# A change in position smaller than this, in percent, is not a movement
MOTION_THRESHOLD = 0.05


class MoveDirection(StrEnum):
    """Direction of a small mask movement."""

    OPEN = "open"
    CLOSE = "close"


class MoveDistance(StrEnum):
    """Size of a small mask movement."""

    JOG = "jog"
    PERCENT = "percent"


MOVE_DISTANCES = {
    MoveDistance.JOG: MovementCode.JOG,
    MoveDistance.PERCENT: MovementCode.MOVE,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SeymourConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Seymour cover entities."""
    runtime_data = entry.runtime_data
    mask_ids = runtime_data.metadata.data.system_info.mask_ids
    async_add_entities(
        SeymourMaskCover(runtime_data, motor_id, members)
        for motor_id, members in mask_targets(mask_ids)
    )


class SeymourMaskCover(SeymourEntity, CoverEntity):
    """A mask motor, or a group of mask motors moving together, as a cover."""

    _attr_device_class = CoverDeviceClass.SHADE
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(
        self,
        runtime_data: SeymourRuntimeData,
        motor_id: MotorID,
        member_motors: list[MotorID],
    ) -> None:
        """Initialize the cover."""
        key = f"mask_{motor_id.name.lower()}"
        self._attr_translation_key = key
        super().__init__(runtime_data, key)
        self._motor_id = motor_id
        self._member_motors = member_motors

    def _extended_pct(self, positions: dict[MotorID, float]) -> float | None:
        """Return the average extension of the member masks, in percent."""
        # Encoder offsets can report a little past either end of the range
        values = [
            min(max(positions[m], 0.0), 100.0)
            for m in self._member_motors
            if m in positions
        ]
        if not values:
            return None
        return sum(values) / len(values)

    @property
    @override
    def current_cover_position(self) -> int | None:
        """Return the cover position, where 100 is fully retracted."""
        if (extended := self._extended_pct(self.coordinator.data.positions)) is None:
            return None
        return round(100 - extended)

    @property
    @override
    def is_closed(self) -> bool | None:
        """Return whether the masks are fully extended."""
        if (extended := self._extended_pct(self.coordinator.data.positions)) is None:
            return None
        return extended >= 100 - CLOSED_TOLERANCE

    def _direction(self) -> MoveDirection | None:
        """Return which way the member masks move, if they do.

        The status says the controller is busy, but not which masks move.
        For moves in or out it does say the direction, which applies to the
        masks seen moving, or that a command sent moving. For homing, preset
        moves, and calibration, the change in position tells the direction.
        """
        data = self.coordinator.data
        code = data.status.code
        if code not in MOVING_STATUS_CODES:
            return None
        now = self._extended_pct(data.positions)
        before = self._extended_pct(data.previous_positions)
        moved = 0.0 if now is None or before is None else now - before
        if code in (StatusCode.MOVING_OUTWARD, StatusCode.MOVING_INWARD):
            if abs(moved) > MOTION_THRESHOLD or self.runtime_data.last_targets & set(
                self._member_motors
            ):
                return (
                    MoveDirection.OPEN
                    if code is StatusCode.MOVING_OUTWARD
                    else MoveDirection.CLOSE
                )
            return None
        if moved < -MOTION_THRESHOLD:
            return MoveDirection.OPEN
        if moved > MOTION_THRESHOLD:
            return MoveDirection.CLOSE
        if code is StatusCode.HOMING and now is not None and now > HOME_TOLERANCE:
            # Homing retracts every mask that isn't home yet
            return MoveDirection.OPEN
        return None

    @property
    @override
    def is_opening(self) -> bool:
        """Return whether the masks are retracting."""
        return self._direction() is MoveDirection.OPEN

    @property
    @override
    def is_closing(self) -> bool:
        """Return whether the masks are extending."""
        return self._direction() is MoveDirection.CLOSE

    async def _async_send(
        self, command: Awaitable[None], *, moves: bool = True
    ) -> None:
        """Send a command to the controller and refresh the status."""
        # The newest command wins over a move to a ratio preset in progress
        self.runtime_data.cancel_preset_move()
        if moves:
            self.runtime_data.last_targets = frozenset(self._member_motors)
        try:
            await command
        except SeymourError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self.coordinator.async_expect_motion()
        await self.coordinator.async_request_refresh()

    @override
    async def async_open_cover(self, **kwargs: Any) -> None:
        """Retract the masks."""
        await self._async_send(
            self.runtime_data.client.move_out(self._motor_id, MovementCode.UNTIL_LIMIT)
        )

    @override
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Extend the masks."""
        await self._async_send(
            self.runtime_data.client.move_in(self._motor_id, MovementCode.UNTIL_LIMIT)
        )

    @override
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the masks."""
        await self._async_send(
            self.runtime_data.client.halt(self._motor_id), moves=False
        )

    async def async_move_mask(
        self, direction: MoveDirection, distance: MoveDistance
    ) -> None:
        """Move the masks a small step, for fine adjustments."""
        movement = MOVE_DISTANCES[distance]
        client = self.runtime_data.client
        await self._async_send(
            client.move_out(self._motor_id, movement)
            if direction is MoveDirection.OPEN
            else client.move_in(self._motor_id, movement)
        )
