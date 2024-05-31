import asyncio
import binascii
import logging
import struct

import homeassistant.util.color as color_util
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import DOMAIN, XiaomiGwDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
        hass: HomeAssistant,
        config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        discovery_info: DiscoveryInfoType = None):
    """Set up the light platform."""
    _LOGGER.info("Setting up light")
    gateway = hass.data[DOMAIN]
    async_add_entities([XiaomiGatewayLight(gateway)])


class XiaomiGatewayLight(XiaomiGwDevice, LightEntity):
    """Representation of a Xiaomi Gateway Light."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, gw):
        """Initialize the light."""
        super().__init__(gw, "light", None, "miio.gateway", "Gateway LED")
        self._hs = (0, 0)
        self._brightness = 100
        self._state = False

        self.update_device_params()

    def update_device_params(self):
        """Update the device parameters."""
        if self._gw.is_available():
            asyncio.create_task(self._send_to_hub({"method": "toggle_light", "params": ["off"]}))

    @property
    def is_on(self) -> bool:
        """Return true if the light is on."""
        return self._state

    @property
    def brightness(self) -> int:
        """Return the brightness of the light."""
        return int(255 * self._brightness / 100)

    @property
    def hs_color(self) -> tuple:
        """Return the hs color value."""
        return self._hs

    @property
    def supported_color_modes(self):
        """Return the supported color modes."""
        return {"hs"}

    async def async_turn_on(self, **kwargs):
        """Turn on or control the light."""
        if ATTR_HS_COLOR in kwargs:
            self._hs = kwargs[ATTR_HS_COLOR]
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = int(100 * kwargs[ATTR_BRIGHTNESS] / 255)
        rgb = color_util.color_hs_to_RGB(*self._hs)
        argb = (self._brightness,) + rgb
        argbhex = binascii.hexlify(struct.pack("BBBB", *argb)).decode("ASCII")
        argbhex = int(argbhex, 16)
        await self._send_to_hub({"method": "set_rgb", "params": [argbhex]})
        self._state = True
        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn off the light."""
        await self._send_to_hub({"method": "toggle_light", "params": ["off"]})
        self._state = False
        self.async_schedule_update_ha_state()

    def parse_incoming_data(self, model, sid, event, params) -> bool:
        """Parse incoming data from gateway."""

        light = params.get("light")
        if light is not None:
            if light == 'on':
                self._state = True
            elif light == 'off':
                self._state = False
            return True

        return False
