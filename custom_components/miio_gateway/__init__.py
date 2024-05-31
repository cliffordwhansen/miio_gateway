import asyncio
import json
import logging
import socket
from datetime import timedelta
from multiprocessing import Queue
from threading import Thread

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import (
    CONF_HOST, CONF_PORT,
    EVENT_HOMEASSISTANT_STOP)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import discovery
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.dt import utcnow

INTERNAL_PING = "internal.PING"

_LOGGER = logging.getLogger(__name__)

TIME_INTERVAL_PING = timedelta(minutes=1)

DOMAIN = "miio_gateway"
CONF_DATA_DOMAIN = "miio_gateway_config"

CONF_SENSORS = "sensors"
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
        vol.Optional(CONF_SENSORS, default={}): vol.Any(cv.ensure_list, [SENSORS_CONFIG_SCHEMA]),
    })
}, extra=vol.ALLOW_EXTRA)

SERVICE_JOIN_ZIGBEE = "join_zigbee"
SERVICE_SCHEMA = vol.Schema({})


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the gateway from config."""
    _LOGGER.info("Starting gateway setup...")

    # Gateway starts its action on object init.
    gateway = XiaomiGw(hass, config[DOMAIN][CONF_HOST], config[DOMAIN][CONF_PORT])

    # Gentle stop on HASS stop.
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, gateway.gently_stop)

    # Share the config to platform's components.
    hass.data[DOMAIN] = gateway
    hass.data[CONF_DATA_DOMAIN] = config[DOMAIN].get(CONF_SENSORS)

    # Load components.
    for component in ["light", "media_player", "binary_sensor", "sensor", "alarm_control_panel"]:
        hass.async_create_task(
            discovery.async_load_platform(hass, component, DOMAIN, {}, config)
        )

    # Zigbee join HASS service helper.
    async def join_zigbee_service_handler(service):
        gateway = hass.data[DOMAIN]
        await gateway.send_to_hub({"method": "start_zigbee_join"})

    hass.services.async_register(
        DOMAIN, SERVICE_JOIN_ZIGBEE, join_zigbee_service_handler,
        schema=SERVICE_SCHEMA
    )

    return True


def _process_result(result):
    if isinstance(result, list):
        if len(result) == 0:
            return "unknown"
        return result[0]
    return result


def _process_params(params):
    if isinstance(params, list):
        if len(params) == 0:
            return {}
        params = params[0]
    if not isinstance(params, dict):
        return {"data": params}
    return params


def _is_ignored_method(method: str) -> bool:
    return method.startswith("internal.") or method in ["_sync.neighborDevInfo"]


def _determine_event(method: str):
    if method.startswith("event."):
        return method
    elif method == "_otc.log":
        return EVENT_METADATA
    elif method == "props":
        return EVENT_VALUES
    return None


def _miio_msg_decode(data: bytes) -> list:
    """Decode data received from gateway."""
    if data[-1] == 0:
        data = data[:-1]
    resps = []
    try:
        data_arr = "[" + data.decode().replace("}{", "},{") + "]"
        resps = json.loads(data_arr)
    except json.JSONDecodeError:
        _LOGGER.warning("Bad JSON received: %s", str(data))
    return resps


class XiaomiGw:
    """Gateway socket and communication layer."""

    def __init__(self, hass: HomeAssistant, host: str, port: int):
        self.hass = hass
        self._host = host
        self._port = port
        self._socket = None
        self._thread = None
        self._thread_alive = True
        self._send_queue = Queue(maxsize=25)
        self._miio_id = 0
        self._callbacks = []
        self._result_callbacks = {}
        self._available = None
        self._availability_pinger = None
        self._pings_sent = 0
        self._known_sids = []
        self._known_sids.append("miio.gateway")  # Append self.

        import hashlib, base64
        self._unique_id = base64.urlsafe_b64encode(
            hashlib.sha1((self._host + ":" + str(self._port)).encode("utf-8")).digest()
        )[:10].decode("utf-8")

        self._create_socket()
        self._init_listener()

    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

    def is_available(self) -> bool:
        """Return availability state."""
        return self._available

    def gently_stop(self, event=None):
        """Stops listener and closes socket."""
        self._stop_listening()
        self._close_socket()

    async def send_to_hub(self, data: dict, callback=None):
        """Send data to hub."""
        miio_id, data = self._miio_msg_encode(data)
        if callback is not None:
            _LOGGER.info("Adding callback for call ID: %s", str(miio_id))
            self._result_callbacks[miio_id] = callback
        self._send_queue.put(data)

    def append_callback(self, callback):
        """Append a callback function."""
        self._callbacks.append(callback)

    def append_known_sid(self, sid):
        """Append a known sensor ID."""
        self._known_sids.append(sid)

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
            _, ping = self._miio_msg_encode({"method": ("%s" % INTERNAL_PING)})
            self._socket.settimeout(0.1)
            self._socket.sendto(ping, (self._host, self._port))
            # Wait for response.
            self._socket.settimeout(5.0)
            self._socket.recvfrom(1480)
            # If didn't timeout - gateway is available.
            self._set_availability(True)
        except socket.timeout:
            # If timed out – gateway is unavailable.
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
        self._thread.start()
        _LOGGER.debug("Starting availability tracker...")
        self._track_availability()

    def _stop_listening(self):
        """Remove loop thread."""
        _LOGGER.debug("Exiting thread...")
        self._thread_alive = False
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
                data = self._socket.recvfrom(1480)[0]  # Will timeout on no data.

                _LOGGER.debug("Received data:")
                _LOGGER.debug(data)

                # We got here in code = we have communication with gateway.
                self._set_availability(True)

                # Get all messages from response data.
                resps = _miio_msg_decode(data)

                # Parse all messages in response.
                self._parse_received_resps(resps)

            except socket.timeout:
                pass
            except socket.error as e:
                _LOGGER.error("Socket error!")
                _LOGGER.error(e)

    def _track_availability(self):
        """Check pings status and schedule next availability check."""
        _LOGGER.debug("Starting to track availability...")
        # Schedule pings every TIME_INTERVAL_PING.
        self._availability_pinger = async_track_time_interval(
            self.hass, self._ping, TIME_INTERVAL_PING
        )

    def _set_availability(self, available: bool):
        """Set availability of the gateway. Inform child devices."""
        was_available = self._available
        availability_changed = (not available and was_available) or (available and not was_available)
        if available:
            self._available = True
            self._pings_sent = 0
        else:
            self._available = False

        if availability_changed:
            _LOGGER.info("Gateway availability changed! Available: %s", str(available))
            for func in self._callbacks:
                func(None, None, EVENT_AVAILABILITY)

    @callback
    async def _ping(self, event=None):
        """Queue ping to keep and check connection."""
        self._pings_sent += 1
        await self.send_to_hub({"method": INTERNAL_PING})
        await asyncio.sleep(6)  # Give it `timeout` time to respond...
        if self._pings_sent >= 3:
            self._set_availability(False)

    def _parse_received_resps(self, resps: list):
        """Parse received data."""
        for res in resps:
            if "result" in res:
                self._handle_result(res)
            elif "method" in res:
                self._handle_method(res)
            else:
                _LOGGER.error("Non-parseable data: %s", str(res))

    def _handle_result(self, res: dict):
        miio_id = res.get("id")
        if miio_id is not None and miio_id in self._result_callbacks:
            result = res.get("result")
            result = _process_result(result)
            self._result_callbacks[miio_id](result)

    def _handle_method(self, res: dict):
        res.setdefault("model", "lumi.gateway.mieu01")
        res.setdefault("sid", "miio.gateway")
        model = res.get("model")
        sid = res.get("sid")
        params = _process_params(res.get("params") or {})

        method = res.get("method")
        if _is_ignored_method(method):
            return

        event = _determine_event(method)
        if event:
            self._trigger_callbacks(model, sid, event, params)
        else:
            _LOGGER.info("Received unknown method: %s", str(method))

    def _trigger_callbacks(self, model, sid, event, params):
        for func in self._callbacks:
            func(model, sid, event, params)

    def _event_received(self, model: str, sid: str, event: str):
        """Callback for receiving sensor event from gateway."""
        _LOGGER.debug("Received event: %s %s - %s", str(model), str(sid), str(event))
        if sid not in self._known_sids:
            _LOGGER.warning("Received event from unregistered sensor: %s %s - %s", str(model), str(sid), str(event))

    def _miio_msg_encode(self, data: dict) -> tuple:
        """Encode data to be sent to gateway."""
        if data.get("method") == INTERNAL_PING:
            msg = data
        else:
            if self._miio_id != 12345:
                self._miio_id += 1
            else:
                self._miio_id += 2
            if self._miio_id > 999999999:
                self._miio_id = 1
            msg = {"id": self._miio_id}
            msg.update(data)
        return self._miio_id, json.dumps(msg).encode()


class XiaomiGwDevice(RestoreEntity):
    """A generic device of Gateway."""

    def __init__(self, gw: XiaomiGw, platform: str, device_class=None, sid=None, name=None, restore=None):
        """Initialize the device."""
        self._gw = gw
        self._send_to_hub = self._gw.send_to_hub
        self._state = None
        self._restore = restore
        self._sid = sid
        self._name = name
        self._model = None
        self._voltage = None
        self._lqi = None
        self._alive = None
        if device_class is None:
            self._unique_id = f"{sid}_{platform}"
            self.entity_id = f"{platform}.{sid.replace('.', '_')}"
        else:
            self._unique_id = f"{sid}_{platform}_{device_class}"
            self.entity_id = f"{platform}.{sid.replace('.', '_')}_{device_class}"

    async def async_added_to_hass(self):
        """Add push data listener for this device."""
        self._gw.append_callback(self._add_push_data_job)
        if self._restore:
            state = await self.async_get_last_state()
            if state is not None:
                self._state = state.state

    @property
    def name(self) -> str:
        """Return the name of the device."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return a unique ID for the device."""
        return self._unique_id

    @property
    def available(self) -> bool:
        """Return the availability of the device."""
        return self._gw.is_available()

    @property
    def should_poll(self) -> bool:
        """Return False as the device should not be polled."""
        return False

    @property
    def extra_state_attributes(self) -> dict:
        """Return the extra state attributes of the device."""
        return {
            ATTR_VOLTAGE: self._voltage,
            ATTR_LQI: self._lqi,
            ATTR_MODEL: self._model,
            ATTR_ALIVE: self._alive,
        }

    def _add_push_data_job(self, *args):
        """Add a job to handle push data."""
        self.hass.add_job(self._push_data, *args)

    @callback
    def _push_data(self, model=None, sid=None, event=None, params=None):
        """Push data that came from gateway to parser. Update HA state if any changes were made."""
        if params is None:
            params = {}
        init_parse = self._pre_parse_data(model, sid, event, params)
        if init_parse is not None:
            if init_parse:
                self.async_schedule_update_ha_state()
            return
        has_data = self.parse_incoming_data(model, sid, event, params)
        if has_data:
            self.async_schedule_update_ha_state()

    def parse_incoming_data(self, model: str, sid: str, event: str, params: dict) -> bool:
        """Parse incoming data from gateway. Abstract method to be implemented by subclasses."""
        raise NotImplementedError()

    def update_device_params(self):
        """Update device parameters if necessary."""
        pass

    def _pre_parse_data(self, model: str, sid: str, event: str, params: dict) -> bool | None:
        """Make initial checks and return bool if parsing shall be ended."""
        if event == EVENT_AVAILABILITY:
            self.update_device_params()
            return True
        if self._sid != sid:
            return False
        if model is not None:
            self._model = model
        if event == EVENT_KEEPALIVE:
            self._alive = utcnow()
            return True
        if event == EVENT_METADATA:
            zigbee_data = params.get("subdev_zigbee")
            if zigbee_data is not None:
                self._voltage = zigbee_data.get("voltage")
                self._lqi = zigbee_data.get("lqi")
                _LOGGER.info("Vol: %s lqi: %s", str(self._voltage), str(self._lqi))
                return True
            return False
        return None
