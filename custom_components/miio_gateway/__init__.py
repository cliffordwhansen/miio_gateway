import json
import logging
import socket
from collections import defaultdict
from queue import Queue
from threading import Thread
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util.dt import utcnow

_LOGGER = logging.getLogger(__name__)

TIME_INTERVAL_PING = timedelta(minutes=1)

DOMAIN = "miio_gateway"
CONF_DATA_DOMAIN = "miio_gateway_config"
PLATFORMS = ["light", "media_player", "binary_sensor", "sensor", "alarm_control_panel"]
ENTRY_DATA_GATEWAY = "gateway"
ENTRY_DATA_SENSORS = "sensors"
ENTRY_DATA_DISCOVERED = "discovered_devices"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_SENSORS = "sensors"
CONF_IGNORED_SIDS = "ignored_sids"
CONF_SENSOR_SID = "sid"
CONF_SENSOR_CLASS = "class"
CONF_SENSOR_NAME = "friendly_name"
CONF_SENSOR_RESTORE = "restore"

ATTR_ALIVE = "heartbeat"
ATTR_VOLTAGE = "voltage"
ATTR_LQI = "link_quality"
ATTR_MODEL = "model"

EVENT_METADATA = "internal.metadata"
EVENT_VALUES = "internal.values"
EVENT_KEEPALIVE = "event.keepalive"
EVENT_AVAILABILITY = "event.availability"

SENSORS_CONFIG_SCHEMA = vol.Schema({
    vol.Optional(CONF_SENSOR_SID): cv.string,
    vol.Optional(CONF_SENSOR_CLASS): cv.string,
    vol.Optional(CONF_SENSOR_NAME): cv.string,
    vol.Optional(CONF_SENSOR_RESTORE, default=False): cv.boolean,
})

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=54321): cv.port,
        vol.Optional(CONF_SENSORS, default=[]): vol.All(cv.ensure_list, [SENSORS_CONFIG_SCHEMA]),
    })
}, extra=vol.ALLOW_EXTRA)

SERVICE_JOIN_ZIGBEE = "join_zigbee"
SERVICE_SCHEMA = vol.Schema({})

async def async_setup(hass, config):
    """Setup gateway from config."""
    hass.data.setdefault(DOMAIN, {})

    async def join_zigbee_service_handler(service):
        for entry_data in hass.data.get(DOMAIN, {}).values():
            gateway = entry_data.get(ENTRY_DATA_GATEWAY)
            if gateway is not None:
                gateway.send_to_hub({"method": "start_zigbee_join"})
                return
        _LOGGER.warning("join_zigbee requested but no miio_gateway instance is loaded")

    if not hass.services.has_service(DOMAIN, SERVICE_JOIN_ZIGBEE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_JOIN_ZIGBEE,
            join_zigbee_service_handler,
            schema=SERVICE_SCHEMA,
        )

    if DOMAIN not in config:
        return True

    _LOGGER.info("Starting gateway setup...")

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data=config[DOMAIN],
        )
    )

    return True


async def async_reload_entry(hass, entry: ConfigEntry):
    """Reload miio_gateway when config entry data/options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def get_configured_sensors(entry: ConfigEntry):
    """Return configured child sensors from options or entry data."""
    return entry.options.get(CONF_SENSORS, entry.data.get(CONF_SENSORS, []))


def get_ignored_sids(entry: ConfigEntry):
    """Return ignored child device SIDs from entry options."""
    return entry.options.get(CONF_IGNORED_SIDS, [])


async def async_setup_entry(hass, entry: ConfigEntry):
    """Set up miio_gateway from a config entry imported from YAML."""
    ignored_sids = set(get_ignored_sids(entry))
    gateway = await hass.async_add_executor_job(
        XiaomiGw, hass, entry.entry_id, entry.data[CONF_HOST], entry.data[CONF_PORT], ignored_sids
    )

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, gateway.gently_stop)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        ENTRY_DATA_GATEWAY: gateway,
        ENTRY_DATA_SENSORS: get_configured_sensors(entry),
        ENTRY_DATA_DISCOVERED: {},
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry: ConfigEntry):
    """Unload a miio_gateway config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if entry_data is not None:
            gateway = entry_data.get(ENTRY_DATA_GATEWAY)
            if gateway is not None:
                await hass.async_add_executor_job(gateway.gently_stop)
    return unload_ok

class XiaomiGw:
    """Gateway socket and communication layer."""

    def __init__(self, hass, entry_id, host, port, ignored_sids=None):
        self.hass = hass
        self._entry_id = entry_id

        self._host = host
        self._port = port

        self._socket = None
        self._thread = None
        self._thread_alive = True

        self._send_queue = Queue(maxsize=25)
        self._miio_id = 0

        self._callbacks = set()
        self._sid_callbacks = defaultdict(set)
        self._result_callbacks = {}

        self._available = None
        self._availability_pinger = None
        self._pings_sent = 0

        self._known_sids = {"miio.gateway"}  # Append self.
        self._ignored_sids = set(ignored_sids or ())
        self._discovered_unknown_devices = set()

        import hashlib, base64
        self._unique_id = base64.urlsafe_b64encode(hashlib.sha1((self._host + ":" + str(self._port)).encode("utf-8")).digest())[:10].decode("utf-8")

        self._create_socket()
        self._init_listener()

    """Public."""

    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

    def is_available(self):
        """Return availability state."""
        return self._available

    def gently_stop(self, event=None):
        """Stops listener and closes socket."""
        if self._availability_pinger is not None:
            self._availability_pinger()
            self._availability_pinger = None
        self._stop_listening()
        self._close_socket()

    def send_to_hub(self, data, callback=None):
        """Send data to hub."""
        miio_id, data = self._miio_msg_encode(data)
        if callback is not None:
            _LOGGER.debug("Adding callback for call ID: %s", miio_id)
            self._result_callbacks[miio_id] = callback
        self._send_queue.put(data)

    def append_callback(self, callback, sid=None):
        if sid is None:
            self._callbacks.add(callback)
        else:
            self._sid_callbacks[sid].add(callback)

    def remove_callback(self, callback, sid=None):
        if sid is None:
            self._callbacks.discard(callback)
            return

        sid_callbacks = self._sid_callbacks.get(sid)
        if sid_callbacks is None:
            return

        sid_callbacks.discard(callback)
        if not sid_callbacks:
            self._sid_callbacks.pop(sid, None)

    def append_known_sid(self, sid):
        self._known_sids.add(sid)

    def append_ignored_sid(self, sid):
        self._ignored_sids.add(sid)

    def _dispatch_callback(self, func, *args):
        """Run gateway callbacks on the Home Assistant event loop."""
        self.hass.loop.call_soon_threadsafe(func, *args)

    def _dispatch_callbacks_for_sid(self, sid, *args):
        """Run only callbacks interested in a specific sid."""
        callbacks = tuple(self._callbacks)
        if sid is not None:
            callbacks += tuple(self._sid_callbacks.get(sid, ()))

        for func in callbacks:
            self._dispatch_callback(func, *args)

    def _dispatch_all_callbacks(self, *args):
        """Run all registered callbacks."""
        callbacks = set(self._callbacks)
        for sid_callbacks in self._sid_callbacks.values():
            callbacks.update(sid_callbacks)

        for func in callbacks:
            self._dispatch_callback(func, *args)

    @callback
    def _start_discovered_device_flow(self, model, sid, event):
        """Launch a config flow for a newly seen unregistered child device."""
        if sid in self._known_sids or sid in self._ignored_sids:
            return

        suggested_class = self._suggest_sensor_class(model, event)
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        if entry_data is not None:
            entry_data[ENTRY_DATA_DISCOVERED][sid] = {
                ATTR_MODEL: model,
                "event": event,
                CONF_SENSOR_CLASS: suggested_class,
            }

        self.hass.async_create_task(
            self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={
                    "source": "integration_discovery",
                    "title_placeholders": {
                        "model": model or "Unknown device",
                        "sid": sid,
                    },
                },
                data={
                    "entry_id": self._entry_id,
                    CONF_SENSOR_SID: sid,
                    ATTR_MODEL: model,
                    "event": event,
                    CONF_SENSOR_CLASS: suggested_class,
                },
            )
        )

    def _suggest_sensor_class(self, model, event):
        """Best-effort guess for the YAML class of a discovered device."""
        model = (model or "").lower()
        event = (event or "").lower()

        if "motion" in model or event == "event.motion":
            return "motion"
        if "magnet" in model:
            return "door"
        if "switch" in model or "button" in model or event.startswith("event.click"):
            return "button"
        if "weather" in model:
            return "temperature"
        if "leak" in model:
            return "leak"
        if "smoke" in model:
            return "smoke"
        if "vibration" in model:
            return "vibration"
        return None

    """Private."""

    def _create_socket(self):
        """Create connection socket."""
        _LOGGER.debug("Creating socket...")
        self._socket = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)

    def _close_socket(self, event=None):
        """Close connection socket."""
        if self._socket is not None:
            _LOGGER.debug("Closing socket...")
            self._socket.close()
            self._socket = None

    def _init_listener(self):
        """Initialize socket connection with first ping. Set availability accordingly."""
        try:
            # Send ping (w/o queue).
            miio_id, ping = self._miio_msg_encode({"method": "internal.PING"})
            self._socket.settimeout(0.1)
            self._socket.sendto(ping, (self._host, self._port))
            # Wait for response.
            self._socket.settimeout(5.0)
            res = self._socket.recvfrom(1480)[0]
            # If didn't timeouted - gateway is available.
            self._set_availability(True)
        except socket.timeout:
            # If timeouted – gateway is unavailable.
            self._set_availability(False)
        except (TypeError, socket.error) as e:
            # Error: gateway configuration may be wrong.
            _LOGGER.error("Socket error! Your gateway configuration may be wrong!")
            _LOGGER.error(e)
            self._set_availability(False)

        # We can start listener for future actions.
        if self._available is not None:
            # We have gateway initial state - now we can run loop thread that does it all.
            self._start_listening()

    def _start_listening(self):
        """Create thread for loop."""
        _LOGGER.debug("Starting thread...")
        self._thread = Thread(target=self._run_socket_thread, args=())
        self._thread.daemon = True
        self._thread.start()
        _LOGGER.debug("Starting availability tracker...")
        self._track_availability()

    def _stop_listening(self):
        """Remove loop thread."""
        _LOGGER.debug("Exiting thread...")
        self._thread_alive = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join()

    def _run_socket_thread(self):
        """Thread loop task."""
        _LOGGER.debug("Starting listener thread...")

        while self._thread_alive:

            if self._socket is None:
                _LOGGER.error("No socket in listener!")
                self._create_socket()
                continue

            try:
                while not self._send_queue.empty():
                    self._socket.settimeout(0.1)
                    data = self._send_queue.get()
                    _LOGGER.debug("Sending data:")
                    _LOGGER.debug(data)
                    self._socket.sendto(data, (self._host, self._port))

                self._socket.settimeout(1)
                data = self._socket.recvfrom(1480)[0] # Will timeout on no data.

                _LOGGER.debug("Received data:")
                _LOGGER.debug(data)

                # We got here in code = we have communication with gateway.
                self._set_availability(True)

                # Get all messages from response data.
                resps = self._miio_msg_decode(data)

                # Parse all messages in response.
                self._parse_received_resps(resps)

            except socket.timeout:
                pass
            except socket.error as e:
                _LOGGER.error("Socket error!")
                _LOGGER.error(e)

    """Gateway availability."""

    def _track_availability(self):
        """Check pings status and schedule next availability check."""
        _LOGGER.debug("Starting to track availability...")
        if self._availability_pinger is not None:
            self._availability_pinger()
        # Schedule pings every TIME_INTERVAL_PING.
        self._availability_pinger = async_track_time_interval(
            self.hass, self._ping, TIME_INTERVAL_PING)

    def _set_availability(self, available):
        """Set availability of the gateway. Inform child devices."""
        was_available = self._available
        availability_changed = (not available and was_available) or (available and not was_available)
        if available:
            self._available = True
            self._pings_sent = 0
        else:
            self._available = False

        if availability_changed:
            _LOGGER.info("Gateway availability changed! Available: " + str(available))
            self._dispatch_all_callbacks(None, None, EVENT_AVAILABILITY)

    @callback
    def _ping(self, event=None):
        """Queue ping to keep and check connection."""
        self._pings_sent = self._pings_sent + 1
        self.send_to_hub({"method": "internal.PING"})

        @callback
        def _mark_unavailable(_now):
            if self._pings_sent >= 3:
                self._set_availability(False)

        async_call_later(self.hass, 6, _mark_unavailable)

    """Miio gateway protocol parsing."""

    def _parse_received_resps(self, resps):
        """Parse received data."""
        for res in resps:

            if "result" in res:
                """Handling request result response."""

                miio_id = res.get("id")
                if miio_id is not None and miio_id in self._result_callbacks:

                    result = res.get("result")
                    # Convert '{"result":["ok"]}' to single value "ok".
                    if isinstance(result, list):
                        # Parse '[]' result.
                        if len(result) == 0:
                            result = "unknown"
                        else:
                            result = result[0]
                    callback = self._result_callbacks.pop(miio_id)
                    self._dispatch_callback(callback, result)

            elif "method" in res:
                """Handling new data received."""

                if "model" not in res:
                    res["model"] = "lumi.gateway.mieu01"
                model = res.get("model")

                if "sid" not in res:
                    res["sid"] = "miio.gateway"
                sid = res.get("sid")

                params = res.get("params")
                if params is None:
                    # Ensure params is dict
                    params = {}
                if isinstance(params, list):
                    # Parse '[]' params
                    if len(params) == 0:
                        # Convert empty list to empty dict
                        params = {}
                    else:
                        # Extract list to dict
                        params = params[0]
                    if not isinstance(params, dict):
                        params = { "data": params }

                method = res.get("method")
                if method.startswith("internal."):
                    """Internal method, nothing to do here."""
                    continue
                elif method in ["_sync.neighborDevInfo"]:
                    """Known but non-handled method."""
                    continue
                elif method.startswith("event."):
                    """Received event."""
                    event = method
                    self._event_received(model, sid, event)
                elif method == "_otc.log":
                    """Received metadata."""
                    event = EVENT_METADATA
                elif method == "props":
                    """Received values."""
                    event = EVENT_VALUES
                else:
                    """Unknown method."""
                    _LOGGER.debug("Received unknown method: %s", method)
                    continue

                # Now we have all the data we need
                self._dispatch_callbacks_for_sid(sid, model, sid, event, params)

            else:
                """Nothing that we can handle."""
                _LOGGER.error("Non-parseable data: " + str(res))

    def _event_received(self, model, sid, event):
        """Callback for receiving sensor event from gateway."""
        _LOGGER.debug("Received event: " + str(model) + " " + str(sid) + " - " + str(event))
        if sid in self._ignored_sids:
            _LOGGER.debug("Ignoring event from ignored sensor: %s %s - %s", model, sid, event)
            return

        if sid not in self._known_sids:
            _LOGGER.warning("Received event from unregistered sensor: " + str(model) + " " + str(sid) + " - " + str(event))
            device_key = (model, sid)
            if device_key not in self._discovered_unknown_devices:
                self._discovered_unknown_devices.add(device_key)
                self.hass.loop.call_soon_threadsafe(
                    self._start_discovered_device_flow,
                    model,
                    sid,
                    event,
                )

    """Miio."""

    def _miio_msg_encode(self, data):
        """Encode data to be sent to gateway."""
        if data.get("method") and data.get("method") == "internal.PING":
            msg = data
        else:
            if self._miio_id != 12345:
                self._miio_id = self._miio_id + 1
            else:
                self._miio_id = self._miio_id + 2
            if self._miio_id > 999999999:
                self._miio_id = 1
            msg = { "id": self._miio_id }
            msg.update(data)
        return([self._miio_id, (json.dumps(msg)).encode()])

    def _miio_msg_decode(self, data):
        """Decode data received from gateway."""

        # Trim `0` from the end of data string.
        if data[-1] == 0:
            data = data[:-1]

        # Prepare array of responses.
        resps = []
        try:
            data_arr = "[" + data.decode().replace("}{", "},{") + "]"
            resps = json.loads(data_arr)
        except:
            _LOGGER.warning("Bad JSON received: " + str(data))
        return resps


class XiaomiGwDevice(RestoreEntity):
    """A generic device of Gateway."""

    _attr_should_poll = False

    def __init__(self, gw, platform, device_class = None, sid = None, name = None, restore = None, device_name = None):
        """Initialize the device."""

        self._gw = gw
        self._send_to_hub = self._gw.send_to_hub

        self._state = None
        self._restore = restore
        self._sid = sid
        self._name = name
        self._device_name = device_name or name

        self._model = None
        self._voltage = None
        self._lqi = None
        self._alive = None

        if device_class is None:
            self._unique_id = "{}_{}".format(sid, platform)
            self.entity_id = platform + "." + sid.replace(".", "_")
        else:
            self._unique_id = "{}_{}_{}".format(sid, platform, device_class)
            self.entity_id = platform + "." + sid.replace(".", "_") + "_" + device_class

    async def async_added_to_hass(self):
        """Add push data listener for this device."""
        self._gw.append_callback(self._push_data, self._sid)
        state = await self.async_get_last_state()
        if state is not None:
            if self._restore:
                self._state = state.state

            attributes = state.attributes
            if attributes.get(ATTR_VOLTAGE) is not None:
                self._voltage = attributes.get(ATTR_VOLTAGE)
            if attributes.get(ATTR_LQI) is not None:
                self._lqi = attributes.get(ATTR_LQI)
            if attributes.get(ATTR_MODEL) is not None:
                self._model = attributes.get(ATTR_MODEL)

    async def async_will_remove_from_hass(self):
        """Remove push data listener for this device."""
        self._gw.remove_callback(self._push_data, self._sid)

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def available(self):
        return self._gw.is_available()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device grouping info for the HA device registry."""
        if self._sid == "miio.gateway":
            return DeviceInfo(
                identifiers={(DOMAIN, self._gw.unique_id())},
                name="Miio Gateway",
                manufacturer="Xiaomi",
                model="lumi.gateway.mieu01",
            )
        return DeviceInfo(
            identifiers={(DOMAIN, self._sid)},
            name=self._device_name or self._sid.replace(".", "_"),
            manufacturer="Xiaomi / Aqara",
            model=self._model,
            via_device=(DOMAIN, self._gw.unique_id()),
        )


    @property
    def extra_state_attributes(self):
        attrs = { ATTR_VOLTAGE: self._voltage, ATTR_LQI: self._lqi, ATTR_MODEL: self._model, ATTR_ALIVE: self._alive }
        return attrs


    @callback
    def _push_data(self, model = None, sid = None, event = None, params = {}):
        """Push data that came from gateway to parser. Update HA state if any changes were made."""

        # If should/need get into real parsing
        init_parse = self._pre_parse_data(model, sid, event, params)
        if init_parse is not None:
            # Update HA state only if data changed
            if init_parse is True:
                self.async_write_ha_state()
            return

        # If parsed some data
        has_data = self.parse_incoming_data(model, sid, event, params)
        if has_data:
            self.async_write_ha_state()

    def parse_incoming_data(self, model, sid, event, params):
        """Parse incoming data from gateway. Abstract."""
        raise NotImplementedError()

    def update_device_params(self):
        """If component needs to read data first to get it's state."""
        pass

    def _pre_parse_data(self, model, sid, event, params):
        """Make initial checks and return bool if parsing shall be ended."""

        # Generic handler for availability change
        # Devices are getting availability state from Gateway itself
        if event == EVENT_AVAILABILITY:
            self.update_device_params()
            return True

        if self._sid != sid:
            return False

        if model is not None:
            self._model = model

        # Generic handler for event.keepalive
        if event == EVENT_KEEPALIVE:
            self._alive = utcnow()
            # Keepalive only updates the heartbeat timestamp — no state write needed.
            return False

        # Generic handler for _otc.log
        if event == EVENT_METADATA:
            zigbeeData = params.get("subdev_zigbee")
            if zigbeeData is not None:
                new_voltage = zigbeeData.get("voltage")
                new_lqi = zigbeeData.get("lqi")
                changed = new_voltage != self._voltage or new_lqi != self._lqi
                self._voltage = new_voltage
                self._lqi = new_lqi
                _LOGGER.debug("Vol:%s lqi:%s", self._voltage, self._lqi)
                return changed
            return False

        return None
