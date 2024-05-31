import asyncio
import logging
from datetime import timedelta

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import (
    MEDIA_TYPE_MUSIC, MediaPlayerEntityFeature)
from homeassistant.const import (
    STATE_IDLE, STATE_PLAYING)
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util.dt import utcnow

from . import DOMAIN, XiaomiGwDevice

_LOGGER = logging.getLogger(__name__)

PLAYING_TIME = timedelta(seconds=10)

SUPPORT_PLAYER = (MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.VOLUME_MUTE |
                  MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.STOP)


async def async_setup_platform(
        hass: HomeAssistant,
        config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        discovery_info: DiscoveryInfoType = None):
    """Set up the sound player platform."""
    _LOGGER.info("Setting up sound player")
    gateway = hass.data[DOMAIN]
    async_add_entities([XiaomiGatewayMediaPlayer(gateway)])


class XiaomiGatewayMediaPlayer(XiaomiGwDevice, MediaPlayerEntity):
    """Representation of a Xiaomi Gateway Media Player."""

    def __init__(self, gw):
        """Initialize the media player."""
        super().__init__(gw, "media_player", None, "miio.gateway", "Gateway Player")
        self._volume = None
        self._muted = False
        self._ringtone = 1
        self._state = STATE_IDLE
        self._player_tracker = None

        self.update_device_params()

    def update_device_params(self):
        """Update the device parameters."""
        if self._gw.is_available():
            asyncio.create_task(
                self._send_to_hub({"method": "get_prop", "params": ["gateway_volume"]}, self._init_set_volume))

    def _init_set_volume(self, result):
        if result is not None:
            _LOGGER.info("Setting volume: %s", result)
            self._volume = int(result) / 100

    async def async_set_volume_level(self, volume):
        """Set volume level."""
        int_volume = int(volume * 100)
        await self._send_to_hub({"method": "set_gateway_volume", "params": [int_volume]})
        self._volume = volume
        self.async_schedule_update_ha_state()

    async def async_mute_volume(self, mute):
        """Mute the volume."""
        await self._send_to_hub({"method": "set_mute", "params": [str(mute).lower()]})
        self._muted = mute
        self.async_schedule_update_ha_state()

    async def async_play_media(self, media_type, media_id, **kwargs):
        """Play media."""
        if media_type == MEDIA_TYPE_MUSIC:
            self._ringtone = media_id
            await self.async_media_play()

    async def async_media_play(self, new_volume=None):
        """Play media."""
        int_volume = int(self._volume * 100)
        if new_volume is not None:
            int_volume = int(new_volume)
        await self._send_to_hub({"method": "play_music_new", "params": [str(self._ringtone), int_volume]})
        self._state = STATE_PLAYING
        self._player_tracker = async_track_point_in_utc_time(
            self.hass, self._async_playing_finished, utcnow() + PLAYING_TIME)
        self.async_schedule_update_ha_state()

    async def async_media_stop(self):
        """Stop media."""
        if self._player_tracker is not None:
            self._player_tracker()
            self._player_tracker = None
        await self._send_to_hub({"method": "set_sound_playing", "params": ["off"]})
        self._state = STATE_IDLE
        self.async_schedule_update_ha_state()

    async def async_media_pause(self):
        """Pause media."""
        await self.async_media_stop()

    @property
    def state(self):
        """Return the state of the media player."""
        return self._state

    @property
    def volume_level(self):
        """Return the volume level."""
        return self._volume

    @property
    def is_volume_muted(self):
        """Return True if volume is muted."""
        return self._muted

    @property
    def media_artist(self):
        """Return the media artist."""
        return "Alarm"

    @property
    def media_title(self):
        """Return the media title."""
        return f"Ringtone {self._ringtone}"

    @property
    def supported_features(self):
        """Return the supported features."""
        return SUPPORT_PLAYER

    @property
    def media_content_type(self):
        """Return the media content type."""
        return MEDIA_TYPE_MUSIC

    @callback
    def _async_playing_finished(self, now):
        """Handle when playing is finished."""
        self._player_tracker = None
        self._state = STATE_IDLE
        self.async_schedule_update_ha_state()

    def parse_incoming_data(self, model, sid, event, params) -> bool:
        """Parse incoming data from gateway."""

        gateway_volume = params.get("gateway_volume")
        if gateway_volume is not None:
            self._volume = gateway_volume / 100
            return True

        return False
