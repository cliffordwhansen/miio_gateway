import logging

import homeassistant.components.alarm_control_panel as alarm
from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature, AlarmControlPanelState

from . import DOMAIN, ENTRY_DATA_GATEWAY, XiaomiGwDevice


_LOGGER = logging.getLogger(__name__)

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    _LOGGER.info("Setting up alarm")
    gateway = hass.data[DOMAIN]
    async_add_entities([XiaomiGatewayAlarm(gateway)])


async def async_setup_entry(hass, entry, async_add_entities):
    _LOGGER.info("Setting up alarm")
    gateway = hass.data[DOMAIN][entry.entry_id][ENTRY_DATA_GATEWAY]
    async_add_entities([XiaomiGatewayAlarm(gateway)])

class XiaomiGatewayAlarm(XiaomiGwDevice, alarm.AlarmControlPanelEntity):

    def __init__(self, gw):
        XiaomiGwDevice.__init__(self, gw, "alarm_control_panel", None, "miio.gateway", "Gateway Alarm")

        # Default to ARMED_AWAY if no volume data was set
        self._state_by_volume = AlarmControlPanelState.ARMED_AWAY
        self._volume = 80
        # How to alarm
        self._ringtone = 1
        self._color = "ff0000"

        self.update_device_params()

    def update_device_params(self):
        if self._gw.is_available():
            self._send_to_hub({"method": "get_prop", "params": ["arming"]}, self._init_set_arming)
            self._send_to_hub({"method": "get_prop", "params": ["alarming_volume"]}, self._init_set_volume)

    def _init_set_arming(self, result):
        if result is not None:
            _LOGGER.debug("SETTING ARMED: %s", result)
            if result == "on":
                self._state = self._state_by_volume
            elif result == "off":
                self._state = AlarmControlPanelState.DISARMED

    def _init_set_volume(self, result):
        if result is not None:
            _LOGGER.debug("SETTING ARMED VOL: %s", result)
            self._volume = int(result)
            self._state_by_volume = self._get_state_by_volume(self._volume)
            if self._is_armed():
                self._state = self._state_by_volume

    async def async_alarm_disarm(self, code=None):
        """Send disarm command."""
        self._disarm()
        self._state = AlarmControlPanelState.DISARMED
        self.async_write_ha_state()

    async def async_alarm_arm_away(self, code=None):
        """Send arm away command."""
        self._volume = 80
        self._arm()
        self._state = AlarmControlPanelState.ARMED_AWAY
        self.async_write_ha_state()

    async def async_alarm_arm_home(self, code=None):
        """Send arm home command."""
        self._volume = 25
        self._arm()
        self._state = AlarmControlPanelState.ARMED_HOME
        self.async_write_ha_state()

    async def async_alarm_arm_night(self, code=None):
        """Send arm night command."""
        self._volume = 15
        self._arm()
        self._state = AlarmControlPanelState.ARMED_NIGHT
        self.async_write_ha_state()

    async def async_alarm_trigger(self, code=None):
        """Trigger the alarm."""
        self._siren()
        self._blink()
        self._state = AlarmControlPanelState.TRIGGERED
        self.async_write_ha_state()

    def _arm(self):
        self._send_to_hub({"method": "set_alarming_volume", "params": [self._volume]})
        self._send_to_hub({"method": "set_sound_playing", "params": ["off"]})
        self._send_to_hub({"method": "set_arming", "params": ["on"]})

    def _disarm(self):
        self._send_to_hub({"method": "set_sound_playing", "params": ["off"]})
        self._send_to_hub({"method": "set_arming", "params": ["off"]})

    def _siren(self):
        # TODO playlist
        self._send_to_hub({"method": "play_music_new", "params": [str(self._ringtone), self._volume]})

    def _blink(self):
        # TODO blink
        argbhex = [int("01" + self._color, 16), int("64" + self._color, 16)]
        self._send_to_hub({"method": "set_rgb", "params": [argbhex[1]]})

    def _is_armed(self):
        return self._state not in (None, AlarmControlPanelState.TRIGGERED, AlarmControlPanelState.DISARMED)

    def _get_state_by_volume(self, volume):
        if volume < 20:
            return AlarmControlPanelState.ARMED_NIGHT
        elif volume < 30:
            return AlarmControlPanelState.ARMED_HOME
        else:
            return AlarmControlPanelState.ARMED_AWAY

    @property
    def alarm_state(self):
        return self._state

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        return (
            AlarmControlPanelEntityFeature.ARM_HOME
            | AlarmControlPanelEntityFeature.ARM_AWAY
            | AlarmControlPanelEntityFeature.ARM_NIGHT
            | AlarmControlPanelEntityFeature.TRIGGER
        )

    def parse_incoming_data(self, model, sid, event, params):

        arming = params.get("arming")
        if arming is not None:
            if arming == "on":
                self._state = self._get_state_by_volume(self._volume)
            elif arming == "off":
                self._state = AlarmControlPanelState.DISARMED
            return True

        alarming_volume = params.get("alarming_volume")
        if alarming_volume is not None:
            self._volume = int(alarming_volume)
            self._state_by_volume = self._get_state_by_volume(self._volume)
            if self._is_armed():
                self._state = self._state_by_volume
                return True

        return False
