import logging
from datetime import timedelta

from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerEntityFeature, MediaPlayerState, MediaType
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util.dt import utcnow

from . import DOMAIN, ENTRY_DATA_GATEWAY, XiaomiGwDevice

_LOGGER = logging.getLogger(__name__)

PLAYING_TIME = timedelta(seconds=10)

SUPPORT_PLAYER = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.STOP
)

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    _LOGGER.info("Setting up sound player")
    gateway = hass.data[DOMAIN]
    async_add_entities([XiaomiGatewayMediaPlayer(gateway)])


async def async_setup_entry(hass, entry, async_add_entities):
    _LOGGER.info("Setting up sound player")
    gateway = hass.data[DOMAIN][entry.entry_id][ENTRY_DATA_GATEWAY]
    async_add_entities([XiaomiGatewayMediaPlayer(gateway)])


class XiaomiGatewayMediaPlayer(XiaomiGwDevice, MediaPlayerEntity):

    def __init__(self, gw):
        XiaomiGwDevice.__init__(self, gw, "media_player", None, "miio.gateway", "Gateway Player")
        self._volume = 0.5
        self._muted = False
        self._ringtone = 1
        self._state = MediaPlayerState.IDLE
        self._player_tracker = None

        self.update_device_params()

    def update_device_params(self):
        if self._gw.is_available():
            self._send_to_hub({ "method": "get_prop", "params": ["gateway_volume"] }, self._init_set_volume)

    def _init_set_volume(self, result):
        if result is not None:
            _LOGGER.debug("SETTING VOL: %s", result)
            self._volume = int(result) / 100

    async def async_set_volume_level(self, volume):
        int_volume = int(volume * 100)
        self._send_to_hub({ "method": "set_gateway_volume", "params": [int_volume] })
        self._volume = volume
        self.async_write_ha_state()

    async def async_mute_volume(self, mute):
        self._send_to_hub({ "method": "set_mute", "params": [str(mute).lower()] })
        self._muted = mute
        self.async_write_ha_state()

    async def async_play_media(self, media_type, media_id, **kwargs):
        if media_type == MediaType.MUSIC:
            self._ringtone = media_id
            await self.async_media_play()

    async def async_media_play(self, new_volume=None):
        int_volume = int(self._volume * 100)
        if new_volume is not None:
            int_volume = int(new_volume)
        self._send_to_hub({ "method": "play_music_new", "params": [str(self._ringtone), int_volume] })
        self._state = MediaPlayerState.PLAYING
        self._player_tracker = async_track_point_in_utc_time(
            self.hass, self._async_playing_finished,
            utcnow() + PLAYING_TIME)
        self.async_write_ha_state()

    async def async_media_stop(self):
        if self._player_tracker is not None:
            self._player_tracker()
            self._player_tracker = None
        self._send_to_hub({ "method": "set_sound_playing", "params": ["off"] })
        self._state = MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_media_pause(self):
        await self.async_media_stop()

    @property
    def state(self):
        return self._state

    @property
    def volume_level(self):
        return self._volume

    @property
    def is_volume_muted(self):
        return self._muted

    @property
    def media_artist(self):
        return "Alarm"

    @property
    def media_title(self):
        return "No " + str(self._ringtone)

    @property
    def supported_features(self):
       return SUPPORT_PLAYER

    @property
    def media_content_type(self):
        return MediaType.MUSIC

    @callback
    def _async_playing_finished(self, now):
        self._player_tracker = None
        self._state = MediaPlayerState.IDLE
        self.async_write_ha_state()

    def parse_incoming_data(self, model, sid, event, params):

        gateway_volume = params.get("gateway_volume")
        if gateway_volume is not None:
            float_volume = gateway_volume / 100
            self._volume = float_volume
            return True

        return False
