import asyncio
import logging

from homeassistant.components.alarm_control_panel import AlarmControlPanelEntity, AlarmControlPanelEntityFeature
from homeassistant.const import (
    STATE_ALARM_ARMED_AWAY, STATE_ALARM_ARMED_HOME, STATE_ALARM_ARMED_NIGHT,
    STATE_ALARM_DISARMED, STATE_ALARM_TRIGGERED)
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
    _LOGGER.info("Setting up alarm control panel")
    gateway = hass.data[DOMAIN]
    async_add_entities([XiaomiGatewayAlarm(gateway)])


class XiaomiGatewayAlarm(XiaomiGwDevice, AlarmControlPanelEntity):
    """Representation of a Xiaomi Gateway Alarm."""

    def __init__(self, gw):
        """Initialize the alarm control panel."""
        super().__init__(gw, "alarm_control_panel", None, "miio.gateway", "Gateway Alarm")

        # Default to ARMED_AWAY if no volume data was set
        self._state_by_volume = STATE_ALARM_ARMED_AWAY
        self._volume = 80
        # How to alarm
        self._ringtone = 1
        self._color = "ff0000"

        self.update_device_params()

    def update_device_params(self):
        """Update the device parameters."""
        if self._gw.is_available():
            asyncio.create_task(self._send_to_hub({"method": "get_prop", "params": ["arming"]}, self._init_set_arming))
            asyncio.create_task(
                self._send_to_hub({"method": "get_prop", "params": ["alarming_volume"]}, self._init_set_volume))

    def _init_set_arming(self, result):
        if result is not None:
            _LOGGER.info("Setting armed state: %s", result)
            self._state = self._state_by_volume if result == "on" else STATE_ALARM_DISARMED

    def _init_set_volume(self, result):
        if result is not None:
            _LOGGER.info("Setting armed volume: %s", result)
            self._volume = int(result)
            self._state_by_volume = self._get_state_by_volume(self._volume)
            if self._is_armed():
                self._state = self._state_by_volume

    async def async_alarm_disarm(self, code=None):
        """Send disarm command."""
        await self._disarm()
        self._state = STATE_ALARM_DISARMED
        self.async_schedule_update_ha_state()

    async def async_alarm_arm_away(self, code=None):
        """Send arm away command."""
        self._volume = 80
        await self._arm()
        self._state = STATE_ALARM_ARMED_AWAY
        self.async_schedule_update_ha_state()

    async def async_alarm_arm_home(self, code=None):
        """Send arm home command."""
        self._volume = 25
        await self._arm()
        self._state = STATE_ALARM_ARMED_HOME
        self.async_schedule_update_ha_state()

    async def async_alarm_arm_night(self, code=None):
        """Send arm night command."""
        self._volume = 15
        await self._arm()
        self._state = STATE_ALARM_ARMED_NIGHT
        self.async_schedule_update_ha_state()

    async def async_alarm_trigger(self, code=None):
        """Trigger the alarm."""
        await self._siren()
        await self._blink()
        self._state = STATE_ALARM_TRIGGERED
        self.async_schedule_update_ha_state()

    async def _arm(self):
        """Arm the alarm."""
        await self._send_to_hub({"method": "set_alarming_volume", "params": [self._volume]})
        await self._send_to_hub({"method": "set_sound_playing", "params": ["off"]})
        await self._send_to_hub({"method": "set_arming", "params": ["on"]})

    async def _disarm(self):
        """Disarm the alarm."""
        await self._send_to_hub({"method": "set_sound_playing", "params": ["off"]})
        await self._send_to_hub({"method": "set_arming", "params": ["off"]})

    async def _siren(self):
        """Activate the siren."""
        await self._send_to_hub({"method": "play_music_new", "params": [str(self._ringtone), self._volume]})

    async def _blink(self):
        """Activate the blink."""
        argbhex = [int("01" + self._color, 16), int("64" + self._color, 16)]
        await self._send_to_hub({"method": "set_rgb", "params": [argbhex[1]]})

    def _is_armed(self) -> bool:
        """Check if the alarm is armed."""
        return self._state not in [STATE_ALARM_TRIGGERED, STATE_ALARM_DISARMED]

    def _get_state_by_volume(self, volume: int) -> str:
        """Get the alarm state based on volume."""
        if volume < 20:
            return STATE_ALARM_ARMED_NIGHT
        elif volume < 30:
            return STATE_ALARM_ARMED_HOME
        else:
            return STATE_ALARM_ARMED_AWAY

    @property
    def state(self):
        """Return the state of the alarm control panel."""
        return self._state

    @property
    def supported_features(self) -> int:
        """Return the supported features."""
        return (
                AlarmControlPanelEntityFeature.ARM_AWAY
                | AlarmControlPanelEntityFeature.ARM_HOME
                | AlarmControlPanelEntityFeature.ARM_NIGHT
                | AlarmControlPanelEntityFeature.TRIGGER
        )

    def parse_incoming_data(self, model, sid, event, params) -> bool:
        """Parse incoming data from gateway."""

        arming = params.get("arming")
        if arming is not None:
            self._state = self._get_state_by_volume(self._volume) if arming == "on" else STATE_ALARM_DISARMED
            return True

        alarming_volume = params.get("alarming_volume")
        if alarming_volume is not None:
            self._volume = int(alarming_volume)
            self._state_by_volume = self._get_state_by_volume(self._volume)
            if self._is_armed():
                self._state = self._state_by_volume
            return True

        return False
