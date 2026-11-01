"""Data update coordinators for the Seymour Masking Screen integration."""

from dataclasses import dataclass
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

from .const import (
    DOMAIN,
    LOGGER,
    METADATA_UPDATE_INTERVAL,
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

# Consecutive failed polls before a repair issue tells the user, and before
# the connection is torn down so the next poll starts from a fresh one.
ISSUE_AFTER_FAILURES = 3
RECONNECT_AFTER_FAILURES = 5
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
        self._issue: str | None = None

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
        if self._issue:
            ir.async_delete_issue(
                self.hass, DOMAIN, issue_id(self._issue, self.config_entry.entry_id)
            )
            self._issue = None

        self.update_interval = (
            UPDATE_INTERVAL_MOVING
            if status.code in MOVING_STATUS_CODES
            else UPDATE_INTERVAL_IDLE
        )
        return SeymourData(
            status=status,
            positions={pos.motor_id: pos.position_pct for pos in positions},
        )

    async def _async_handle_failure(self, err: SeymourError) -> None:
        """Track consecutive failures, tell the user, and reset the connection."""
        self._failures += 1
        issue = (
            ISSUE_NO_RESPONSE
            if isinstance(err, SeymourTimeoutError)
            else ISSUE_UNREACHABLE
        )
        if self._failures >= ISSUE_AFTER_FAILURES and issue != self._issue:
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
                severity=ir.IssueSeverity.ERROR,
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
