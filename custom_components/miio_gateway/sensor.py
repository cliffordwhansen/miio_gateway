import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import DOMAIN, CONF_DATA_DOMAIN, CONF_SENSOR_SID, CONF_SENSOR_CLASS, CONF_SENSOR_NAME, CONF_SENSOR_RESTORE, \
    XiaomiGwDevice

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPES = {
    SensorDeviceClass.ILLUMINANCE: {"unit_of_measurement": "lm", "icon": "mdi:white-balance-sunny"},
    SensorDeviceClass.TEMPERATURE: {"unit_of_measurement": UnitOfTemperature.CELSIUS, "icon": "mdi:thermometer"},
    SensorDeviceClass.HUMIDITY: {"unit_of_measurement": "%", "icon": "mdi:water-percent"},
    SensorDeviceClass.PRESSURE: {"unit_of_measurement": "hPa", "icon": "mdi:weather-windy"},
}


async def async_setup_platform(
        hass: HomeAssistant,
        config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        discovery_info: DiscoveryInfoType = None):
    """Set up the sensors."""
    _LOGGER.info("Setting up sensors")

    # Make a list of default + custom device classes
    all_device_classes = list(SENSOR_TYPES.keys())

    gateway = hass.data[DOMAIN]
    entities = []

    # Gateway's illuminance sensor
    entities.append(
        XiaomiGwSensor(gateway, SensorDeviceClass.ILLUMINANCE, "miio.gateway", "Gateway Illuminance Sensor", False))

    for cfg in hass.data[CONF_DATA_DOMAIN]:
        if not cfg:
            cfg = {}

        sid = cfg.get(CONF_SENSOR_SID)
        device_class = cfg.get(CONF_SENSOR_CLASS)
        name = cfg.get(CONF_SENSOR_NAME)
        restore = cfg.get(CONF_SENSOR_RESTORE)

        if sid is None or device_class is None:
            continue

        gateway.append_known_sid(sid)

        if device_class in all_device_classes:
            _LOGGER.info("Registering %s sid %s as sensor", device_class, sid)
            entities.append(XiaomiGwSensor(gateway, device_class, sid, name, restore))

    if not entities:
        _LOGGER.info("No sensors configured")
        return

    async_add_entities(entities)


class XiaomiGwSensor(XiaomiGwDevice, SensorEntity):
    """Representation of a Xiaomi Gateway Sensor."""

    def __init__(self, gw, device_class, sid, name, restore):
        """Initialize the sensor."""
        super().__init__(gw, "sensor", device_class, sid, name, restore)
        self._device_class = device_class

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def device_class(self):
        """Return the class of this device."""
        return self._device_class

    @property
    def icon(self):
        """Return the icon to use in the frontend, if any."""
        return SENSOR_TYPES.get(self._device_class, {}).get("icon")

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return SENSOR_TYPES.get(self._device_class, {}).get("unit_of_measurement")

    def parse_incoming_data(self, model, sid, event, params) -> bool:
        """Parse incoming data from gateway."""

        if self._device_class == SensorDeviceClass.ILLUMINANCE:
            illumination = params.get("illumination")
            if illumination is not None:
                self._state = illumination
                return True

        elif self._device_class == SensorDeviceClass.TEMPERATURE:
            temperature = params.get("temperature")
            if temperature is not None:
                self._state = round(temperature / 100, 1)
                return True

        elif self._device_class == SensorDeviceClass.HUMIDITY:
            humidity = params.get("humidity")
            if humidity is not None:
                self._state = round(humidity / 100, 1)
                return True

        elif self._device_class == SensorDeviceClass.PRESSURE:
            pressure = params.get("pressure")
            if pressure is not None:
                self._state = round(pressure / 100, 1)
                return True

        return False
