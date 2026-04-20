import logging
import binascii
import struct

from homeassistant.components.light import (
    ATTR_BRIGHTNESS, ATTR_HS_COLOR, ColorMode, LightEntity)
import homeassistant.util.color as color_util

from . import DOMAIN, ENTRY_DATA_GATEWAY, XiaomiGwDevice

_LOGGER = logging.getLogger(__name__)

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    gateway = hass.data[DOMAIN]
    async_add_entities([XiaomiGatewayLight(gateway)])


async def async_setup_entry(hass, entry, async_add_entities):
    gateway = hass.data[DOMAIN][entry.entry_id][ENTRY_DATA_GATEWAY]
    async_add_entities([XiaomiGatewayLight(gateway)])

class XiaomiGatewayLight(XiaomiGwDevice, LightEntity):

    def __init__(self, gw):
        XiaomiGwDevice.__init__(self, gw, "light", None, "miio.gateway", "Gateway LED")
        self._attr_supported_color_modes = {ColorMode.HS}
        self._hs = (0, 0)
        self._brightness = 100
        self._state = False

        self.update_device_params()

    def update_device_params(self):
        if self._gw.is_available():
            self._send_to_hub({ "method": "toggle_light", "params": ["off"] })

    @property
    def is_on(self):
        return self._state

    @property
    def brightness(self):
        return int(255 * self._brightness / 100)

    @property
    def hs_color(self):
        return self._hs

    @property
    def color_mode(self):
        return ColorMode.HS

    async def async_turn_on(self, **kwargs):
        if ATTR_HS_COLOR in kwargs:
            self._hs = kwargs[ATTR_HS_COLOR]
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = int(100 * kwargs[ATTR_BRIGHTNESS] / 255)
        rgb = color_util.color_hs_to_RGB(*self._hs)
        argb = (self._brightness,) + rgb
        argbhex = binascii.hexlify(struct.pack("BBBB", *argb)).decode("ASCII")
        argbhex = int(argbhex, 16)
        self._send_to_hub({ "method": "set_rgb", "params": [argbhex] })
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._send_to_hub({ "method": "toggle_light", "params": ["off"] })
        self._state = False
        self.async_write_ha_state()

    def parse_incoming_data(self, model, sid, event, params):

        light = params.get("light")
        if light is not None:
            if light == 'on':
                self._state = True
            elif light == 'off':
                self._state = False
            return True

        return False
