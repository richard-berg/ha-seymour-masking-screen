"""Constants for the Seymour Masking Screen integration."""

from datetime import timedelta
import logging

DOMAIN = "seymour_masking_screen"
LOGGER = logging.getLogger(__package__)

MANUFACTURER = "Seymour-Screen Excellence"

# Movement takes several seconds, so poll faster while a mask is moving to
# keep the reported position close to reality.
UPDATE_INTERVAL_IDLE = timedelta(seconds=5)
UPDATE_INTERVAL_MOVING = timedelta(seconds=1)
# The controller takes a moment to report that masks move, so after a command
# polls stay fast for this long even while it still reports them at rest.
MOTION_GRACE = timedelta(seconds=10)
# The ratio presets only change when someone edits them.
METADATA_UPDATE_INTERVAL = timedelta(minutes=5)

# Homing before a ratio move takes a few seconds; allow for a slow screen.
HOMING_TIMEOUT = 60.0
HOMING_POLL_INTERVAL = 1.0
# The controller takes a moment to report that the masks started moving; until
# then, a status at rest doesn't mean the homing is over.
HOMING_START_TIMEOUT = 5.0

ATTR_DIRECTION = "direction"
ATTR_DISTANCE = "distance"
