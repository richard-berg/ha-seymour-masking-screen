"""Data update coordinators for the Seymour Masking Screen integration."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import override

from seymourlib.client import SeymourClient
from seymourlib.exceptions import SeymourError, SeymourTimeoutError
from seymourlib.protocol import (
    MaskStatus,
    MotorID,
    RatioSetting,
    StatusCode,
    SystemInfo,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    LOGGER,
    METADATA_UPDATE_INTERVAL,
    MOTION_GRACE,
    UPDATE_INTERVAL_IDLE,
    UPDATE_INTERVAL_MOVING,
)

type SeymourConfigEntry = ConfigEntry[SeymourRuntimeData]

MOVING_STATUS_CODES = frozenset(
    {
        StatusCode.MOVING_INWARD,
        StatusCode.MOVING_OUTWARD,
        StatusCode.MOVING_TO_RATIO,
        StatusCode.HOMING,
        StatusCode.CALIBRATING,
    }
)

# How long polls must fail before a repair issue tells the user. A screen
# that's switched off with the projector is unreachable for hours every day,
# so short outages aren't worth a repair.
ISSUE_AFTER = timedelta(minutes=15)
# Consecutive failed polls before the connection is torn down, so the next
# poll starts from a fresh one.
RECONNECT_AFTER_FAILURES = 5
# While the screen is unreachable, polls back off up to this interval; a
# command still reaches it right away, and its refresh resumes normal polling.
MAX_FAILURE_INTERVAL = timedelta(minutes=1)
ISSUE_UNREACHABLE = "unreachable"
# The bridge accepted the connection, but the controller didn't answer
ISSUE_NO_RESPONSE = "no_response"
ISSUES = (ISSUE_UNREACHABLE, ISSUE_NO_RESPONSE)


def issue_id(issue: str, entry_id: str) -> str:
    """Return the ID of a repair issue of a config entry."""
    return f"{issue}_{entry_id}"


@dataclass(slots=True)
class SeymourData:
    """Current status of the masking screen."""

    status: MaskStatus
    positions: dict[MotorID, float]
    # The positions of the poll before, to tell which way masks move
    previous_positions: dict[MotorID, float] = field(default_factory=dict)


@dataclass(slots=True)
class SeymourMetadata:
    """Slowly changing information about the masking screen."""

    system_info: SystemInfo
    ratios: dict[str, RatioSetting]


class SeymourStatusCoordinator(DataUpdateCoordinator[SeymourData]):
    """Poll the status and mask positions of a Seymour controller."""

    config_entry: SeymourConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SeymourConfigEntry,
        client: SeymourClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_status",
            update_interval=UPDATE_INTERVAL_IDLE,
        )
        self.client = client
        self._failures = 0
        self._failing_since: datetime | None = None
        self._issue: str | None = None
        self._motion_expected_until: datetime | None = None

    @override
    async def _async_update_data(self) -> SeymourData:
        """Fetch the status and positions from the controller."""
        try:
            status = await self.client.get_status()
            positions = await self.client.get_positions()
        except SeymourError as err:
            await self._async_handle_failure(err)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        self._failures = 0
        self._failing_since = None
        if self._issue:
            ir.async_delete_issue(
                self.hass, DOMAIN, issue_id(self._issue, self.config_entry.entry_id)
            )
            self._issue = None

        motion_expected = (
            self._motion_expected_until is not None
            and dt_util.utcnow() < self._motion_expected_until
        )
        self.update_interval = (
            UPDATE_INTERVAL_MOVING
            if status.code in MOVING_STATUS_CODES or motion_expected
            else UPDATE_INTERVAL_IDLE
        )
        return SeymourData(
            status=status,
            positions={pos.motor_id: pos.position_pct for pos in positions},
            previous_positions=self.data.positions if self.data else {},
        )

    def async_expect_motion(self) -> None:
        """Poll fast for a while after a command, until the masks show motion."""
        self._motion_expected_until = dt_util.utcnow() + MOTION_GRACE
        self.update_interval = UPDATE_INTERVAL_MOVING

    async def _async_handle_failure(self, err: SeymourError) -> None:
        """Track consecutive failures, tell the user, and reset the connection."""
        self._failures += 1
        now = dt_util.utcnow()
        if self._failing_since is None:
            self._failing_since = now
        # Each failed poll can take half a minute of timeouts; polling at the
        # normal rate would keep the bridge busy and fill the log.
        self.update_interval = min(
            UPDATE_INTERVAL_IDLE * 2 ** min(self._failures - 1, 8),
            MAX_FAILURE_INTERVAL,
        )
        issue = (
            ISSUE_NO_RESPONSE
            if isinstance(err, SeymourTimeoutError)
            else ISSUE_UNREACHABLE
        )
        if now - self._failing_since >= ISSUE_AFTER and issue != self._issue:
            entry_id = self.config_entry.entry_id
            if self._issue:
                ir.async_delete_issue(
                    self.hass, DOMAIN, issue_id(self._issue, entry_id)
                )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id(issue, entry_id),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=issue,
                translation_placeholders={"title": self.config_entry.title},
            )
            self._issue = issue
        if self._failures % RECONNECT_AFTER_FAILURES == 0:
            # The client reconnects on its own, but dropping the connection
            # also releases an IP2SL connection that is stuck, so the next
            # poll gets a fresh one. close() would end the client for good.
            # Persistent garbled responses count too: a fresh connection
            # costs little, and may be what clears them.
            LOGGER.debug("Resetting the connection after %s failures", self._failures)
            await self.client.disconnect()


class SeymourMetadataCoordinator(DataUpdateCoordinator[SeymourMetadata]):
    """Poll the system information and ratio presets of a Seymour controller."""

    config_entry: SeymourConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SeymourConfigEntry,
        client: SeymourClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_metadata",
            update_interval=METADATA_UPDATE_INTERVAL,
        )
        self.client = client

    @override
    async def _async_update_data(self) -> SeymourMetadata:
        """Fetch the system information and ratio presets."""
        try:
            system_info = await self.client.get_system_info()
            ratios = await self.client.get_ratio_settings()
        except SeymourError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        return SeymourMetadata(
            system_info=system_info,
            ratios={setting.ratio.id: setting for setting in ratios},
        )


@dataclass(slots=True)
class SeymourRuntimeData:
    """Runtime data of a Seymour config entry."""

    client: SeymourClient
    status: SeymourStatusCoordinator
    metadata: SeymourMetadataCoordinator
    # A move to a ratio preset takes several commands; see the select
    preset_move: asyncio.Task[None] | None = None
    # The masks the last cover command moved, since the status doesn't say
    last_targets: frozenset[MotorID] = frozenset()

    def cancel_preset_move(self) -> None:
        """Stop a move to a ratio preset in progress, for a newer command."""
        if self.preset_move is not None and not self.preset_move.done():
            self.preset_move.cancel()
