import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory

from . import DOMAIN, CONF_DATA_DOMAIN, CONF_SENSOR_SID, CONF_SENSOR_CLASS, CONF_SENSOR_NAME, CONF_SENSOR_RESTORE, ENTRY_DATA_GATEWAY, ENTRY_DATA_SENSORS, XiaomiGwDevice

_LOGGER = logging.getLogger(__name__)

DEVICE_CLASS_ILLUMINANCE = "illuminance"
DEVICE_CLASS_TEMPERATURE = "temperature"
DEVICE_CLASS_HUMIDITY = "humidity"
DEVICE_CLASS_PRESSURE = "pressure"
DEVICE_CLASS_BATTERY = "battery"
ATTR_BATTERY_PROFILE = "battery_profile"
ATTR_BATTERY_TYPE = "battery_type"

SENSOR_TYPES = {
    DEVICE_CLASS_ILLUMINANCE: {"unit_of_measurement": "lm", "icon": "mdi:white-balance-sunny"},
    DEVICE_CLASS_TEMPERATURE: {"unit_of_measurement": "°C", "icon": "mdi:thermometer"},
    DEVICE_CLASS_HUMIDITY: {"unit_of_measurement": "%", "icon": "mdi:water-percent"},
    DEVICE_CLASS_PRESSURE: {"unit_of_measurement": "hPa", "icon": "mdi:weather-windy"},
}
SUPPORTED_SENSOR_CLASSES = set(SENSOR_TYPES)

BATTERY_PROFILES = {
    "cr2450": {"battery_type": "CR2450", "min_voltage": 2700, "max_voltage": 3200},
    "cr2032": {"battery_type": "CR2032", "min_voltage": 2850, "max_voltage": 3000},
    "coin_cell": {"battery_type": "Coin cell", "min_voltage": 2850, "max_voltage": 3000},
}

CLASS_TO_BATTERY_PROFILE = {
    "motion": "cr2450",
    "button": "cr2032",
    "door": "coin_cell",
    "garage_door": "coin_cell",
    "window": "coin_cell",
    "opening": "coin_cell",
    "leak": "coin_cell",
    "smoke": "coin_cell",
    "vibration": "coin_cell",
}
DEFAULT_BATTERY_PROFILE = "coin_cell"


def _battery_percentage_from_voltage(voltage_mv, battery_profile):
    """Convert raw millivolts to a Home Assistant battery percentage."""
    if voltage_mv is None:
        return None

    profile = BATTERY_PROFILES.get(battery_profile, BATTERY_PROFILES[DEFAULT_BATTERY_PROFILE])

    try:
        voltage = float(voltage_mv)
    except (TypeError, ValueError):
        return None

    min_voltage = profile["min_voltage"]
    max_voltage = profile["max_voltage"]

    if voltage >= max_voltage:
        return 100
    if voltage <= min_voltage:
        return 0

    return round(((voltage - min_voltage) / (max_voltage - min_voltage) * 100), 1)

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    gateway = hass.data[DOMAIN]
    sensor_configs = hass.data[CONF_DATA_DOMAIN]
    await _async_add_sensor_entities(gateway, sensor_configs, async_add_entities)


async def async_setup_entry(hass, entry, async_add_entities):
    entry_data = hass.data[DOMAIN][entry.entry_id]
    await _async_add_sensor_entities(
        entry_data[ENTRY_DATA_GATEWAY],
        entry_data[ENTRY_DATA_SENSORS],
        async_add_entities,
    )


async def _async_add_sensor_entities(gateway, sensor_configs, async_add_entities):
    _LOGGER.info("Setting up sensors")
    entities = []
    battery_sids = set()

    # Gateways's illuminace sensor
    entities.append(XiaomiGwSensor(gateway, DEVICE_CLASS_ILLUMINANCE, "miio.gateway", "Gateway Illuminance Sensor", False))

    for cfg in sensor_configs:
        if not cfg:
            cfg = {}

        sid = cfg.get(CONF_SENSOR_SID)
        device_class = cfg.get(CONF_SENSOR_CLASS)
        name = cfg.get(CONF_SENSOR_NAME)
        restore = cfg.get(CONF_SENSOR_RESTORE)

        if sid is None or device_class is None:
            continue

        gateway.append_known_sid(sid)

        if sid not in battery_sids:
            battery_sids.add(sid)
            battery_name = f"{name} Battery" if name else None
            entities.append(XiaomiGwBatterySensor(gateway, sid, device_class, battery_name))

        if device_class not in SUPPORTED_SENSOR_CLASSES:
            continue

        _LOGGER.info("Registering " + str(device_class) + " sid " + str(sid) + " as sensor")
        entities.append(XiaomiGwSensor(gateway, device_class, sid, name, restore))

    if not entities:
        _LOGGER.info("No sensors configured")
        return False

    async_add_entities(entities)
    return True

class XiaomiGwSensor(XiaomiGwDevice, SensorEntity):

    def __init__(self, gw, device_class, sid, name, restore):
        XiaomiGwDevice.__init__(self, gw, "sensor", device_class, sid, name, restore)

        self._device_class = device_class

    @property
    def native_value(self):
        return self._state

    @property
    def device_class(self):
        return self._device_class

    @property
    def icon(self):
        sensor_type = SENSOR_TYPES.get(self._device_class)
        if sensor_type is None:
            return None
        return sensor_type.get("icon")

    @property
    def native_unit_of_measurement(self):
        sensor_type = SENSOR_TYPES.get(self._device_class)
        if sensor_type is None:
            return None
        return sensor_type.get("unit_of_measurement")

    def parse_incoming_data(self, model, sid, event, params):
        
        if self._device_class == DEVICE_CLASS_ILLUMINANCE:
            illumination = params.get("illumination")
            if illumination is not None:
                self._state = illumination
                return True

        elif self._device_class == DEVICE_CLASS_TEMPERATURE:
            temperature = params.get("temperature")
            if temperature is not None:
                self._state = round(temperature/100, 1)
                return True

        elif self._device_class == DEVICE_CLASS_HUMIDITY:
            humidity = params.get("humidity")
            if humidity is not None:
                self._state = round(humidity/100, 1)
                return True

        elif self._device_class == DEVICE_CLASS_PRESSURE:
            pressure = params.get("pressure")
            if pressure is not None:
                self._state = round(pressure/100, 1)
                return True

        return False


class XiaomiGwBatterySensor(XiaomiGwDevice, SensorEntity):
    """Diagnostic battery sensor derived from restored/live device voltage."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, gw, sid, configured_class, name):
        device_name = name[:-8] if name and name.endswith(" Battery") else name
        XiaomiGwDevice.__init__(self, gw, "sensor", DEVICE_CLASS_BATTERY, sid, name, False, device_name=device_name)
        self._configured_class = configured_class
        self._battery_profile = CLASS_TO_BATTERY_PROFILE.get(configured_class, DEFAULT_BATTERY_PROFILE)

    @property
    def native_value(self):
        return _battery_percentage_from_voltage(self._voltage, self._battery_profile)

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        profile = BATTERY_PROFILES.get(self._battery_profile, BATTERY_PROFILES[DEFAULT_BATTERY_PROFILE])
        attrs.update({
            ATTR_BATTERY_PROFILE: self._battery_profile,
            ATTR_BATTERY_TYPE: profile["battery_type"],
        })
        return attrs

    def parse_incoming_data(self, model, sid, event, params):
        return False

