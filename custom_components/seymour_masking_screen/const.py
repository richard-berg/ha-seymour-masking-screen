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
# The ratio presets only change when someone edits them.
METADATA_UPDATE_INTERVAL = timedelta(minutes=5)

# Homing before a ratio move takes a few seconds; allow for a slow screen.
HOMING_TIMEOUT = 60.0
HOMING_POLL_INTERVAL = 1.0

ATTR_DIRECTION = "direction"
ATTR_DISTANCE = "distance"
