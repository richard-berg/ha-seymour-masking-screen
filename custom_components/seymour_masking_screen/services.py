"""Actions for the Seymour Masking Screen integration."""

import probatio

from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.const import ATTR_OPTION
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, service

from .const import ATTR_DIRECTION, ATTR_DISTANCE, DOMAIN
from .cover import MoveDirection, MoveDistance

SERVICE_MOVE_MASK = "move_mask"
SERVICE_RESET_RATIO = "reset_ratio"
SERVICE_SAVE_RATIO = "save_ratio"


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions of the integration."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_MOVE_MASK,
        entity_domain=COVER_DOMAIN,
        schema={
            probatio.Required(ATTR_DIRECTION): probatio.Coerce(MoveDirection),
            probatio.Required(ATTR_DISTANCE): probatio.Coerce(MoveDistance),
        },
        func="async_move_mask",
    )
    for service_name, func in (
        (SERVICE_SAVE_RATIO, "async_save_ratio"),
        (SERVICE_RESET_RATIO, "async_reset_ratio"),
    ):
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            service_name,
            entity_domain=SELECT_DOMAIN,
            schema={probatio.Required(ATTR_OPTION): cv.string},
            func=func,
        )
