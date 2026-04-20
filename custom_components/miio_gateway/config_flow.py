from __future__ import annotations

from homeassistant import config_entries
import voluptuous as vol

from . import (
    CONF_HOST,
    CONF_PORT,
    CONF_SENSOR_CLASS,
    CONF_SENSOR_NAME,
    CONF_SENSOR_RESTORE,
    CONF_SENSOR_SID,
    CONF_SENSORS,
    DOMAIN,
    ENTRY_DATA_DISCOVERED,
    get_configured_sensors,
)


class MiioGatewayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for miio_gateway."""

    VERSION = 1

    def __init__(self):
        self._discovery_info = None

    @staticmethod
    def async_get_options_flow(config_entry):
        return MiioGatewayOptionsFlow()

    async def async_step_user(self, user_input=None):
        """Handle UI setup for a gateway."""
        errors = {}

        if user_input is not None:
            unique_id = f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Miio Gateway ({user_input[CONF_HOST]})",
                data={
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_SENSORS: [],
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=54321): int,
                }
            ),
            errors=errors,
        )

    async def async_step_import(self, import_config):
        """Import configuration from configuration.yaml."""
        unique_id = f"{import_config[CONF_HOST]}:{import_config[CONF_PORT]}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates=import_config)
        return self.async_create_entry(
            title=f"Miio Gateway ({import_config[CONF_HOST]})",
            data={
                CONF_HOST: import_config[CONF_HOST],
                CONF_PORT: import_config[CONF_PORT],
                CONF_SENSORS: import_config.get(CONF_SENSORS, []),
            },
        )

    async def async_step_integration_discovery(self, discovery_info):
        """Handle discovery of a new child device for an existing gateway."""
        self._discovery_info = discovery_info
        entry = self.hass.config_entries.async_get_entry(discovery_info["entry_id"])
        if entry is None:
            return self.async_abort(reason="device_not_found")

        configured_sids = {
            cfg.get(CONF_SENSOR_SID)
            for cfg in get_configured_sensors(entry)
        }
        if discovery_info[CONF_SENSOR_SID] in configured_sids:
            return self.async_abort(reason="already_configured_device")

        return await self.async_step_confirm_discovery()

    async def async_step_confirm_discovery(self, user_input=None):
        """Confirm adding the discovered child device to the existing entry."""
        if self._discovery_info is None:
            return self.async_abort(reason="device_not_found")

        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._discovery_info["entry_id"])
            if entry is None:
                return self.async_abort(reason="device_not_found")

            sensors = list(get_configured_sensors(entry))
            sensors.append(
                {
                    CONF_SENSOR_SID: self._discovery_info[CONF_SENSOR_SID],
                    CONF_SENSOR_CLASS: user_input[CONF_SENSOR_CLASS],
                    CONF_SENSOR_NAME: user_input.get(CONF_SENSOR_NAME) or None,
                    CONF_SENSOR_RESTORE: user_input[CONF_SENSOR_RESTORE],
                }
            )
            self.hass.config_entries.async_update_entry(entry, options={CONF_SENSORS: sensors})

            entry_data = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if entry_data is not None:
                entry_data.get(ENTRY_DATA_DISCOVERED, {}).pop(self._discovery_info[CONF_SENSOR_SID], None)

            return self.async_abort(reason="device_added")

        return self.async_show_form(
            step_id="confirm_discovery",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SENSOR_CLASS, default=self._discovery_info.get(CONF_SENSOR_CLASS) or ""): str,
                    vol.Optional(CONF_SENSOR_NAME, default=""): str,
                    vol.Optional(CONF_SENSOR_RESTORE, default=False): bool,
                }
            ),
            description_placeholders={
                "sid": self._discovery_info[CONF_SENSOR_SID],
                "model": self._discovery_info.get("model") or "unknown",
                "event": self._discovery_info.get("event") or "unknown",
            },
        )


class MiioGatewayOptionsFlow(config_entries.OptionsFlow):
    """Options flow for managing configured and discovered child devices."""

    def __init__(self):
        self._selected_sid = None

    # ------------------------------------------------------------------ menu

    async def async_step_init(self, user_input=None):
        """Always show the management menu."""
        menu_options = []
        if self._get_discovered_devices():
            menu_options.append("add_discovered_device")
        if list(get_configured_sensors(self.config_entry)):
            menu_options.append("remove_configured_device")

        if not menu_options:
            return self.async_abort(reason="no_discovered_devices")

        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
        )

    # ------------------------------------------------------------------ add

    async def async_step_add_discovered_device(self, user_input=None):
        """Choose a discovered device to add."""
        discovered = self._get_discovered_devices()
        if not discovered:
            return self.async_abort(reason="no_discovered_devices")

        if user_input is not None:
            self._selected_sid = user_input[CONF_SENSOR_SID]
            return await self.async_step_configure_discovered_device()

        choices = {
            sid: f"{sid}  —  {info.get('model') or 'unknown model'}"
            for sid, info in discovered.items()
        }
        return self.async_show_form(
            step_id="add_discovered_device",
            data_schema=vol.Schema({
                vol.Required(CONF_SENSOR_SID): vol.In(choices),
            }),
        )

    async def async_step_configure_discovered_device(self, user_input=None):
        """Confirm class/name for the selected discovered device."""
        discovered = self._get_discovered_devices()
        device_info = discovered.get(self._selected_sid)
        if device_info is None:
            return self.async_abort(reason="device_not_found")

        suggested_class = device_info.get(CONF_SENSOR_CLASS) or ""

        if user_input is not None:
            sensors = list(get_configured_sensors(self.config_entry))
            sensors.append(
                {
                    CONF_SENSOR_SID: self._selected_sid,
                    CONF_SENSOR_CLASS: user_input[CONF_SENSOR_CLASS],
                    CONF_SENSOR_NAME: user_input.get(CONF_SENSOR_NAME) or None,
                    CONF_SENSOR_RESTORE: user_input[CONF_SENSOR_RESTORE],
                }
            )
            return self.async_create_entry(data={CONF_SENSORS: sensors})

        return self.async_show_form(
            step_id="configure_discovered_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SENSOR_CLASS, default=suggested_class): str,
                    vol.Optional(CONF_SENSOR_NAME, default=""): str,
                    vol.Optional(CONF_SENSOR_RESTORE, default=False): bool,
                }
            ),
            description_placeholders={
                "sid": self._selected_sid,
                "model": device_info.get("model") or "unknown",
                "event": device_info.get("event") or "unknown",
            },
        )

    # ----------------------------------------------------------------- remove

    async def async_step_remove_configured_device(self, user_input=None):
        """Choose a configured device to remove."""
        sensors = list(get_configured_sensors(self.config_entry))
        if not sensors:
            return self.async_abort(reason="no_configured_devices")

        if user_input is not None:
            sid_to_remove = user_input[CONF_SENSOR_SID]
            updated = [s for s in sensors if s.get(CONF_SENSOR_SID) != sid_to_remove]
            return self.async_create_entry(data={CONF_SENSORS: updated})

        choices = {}
        for s in sensors:
            sid = s.get(CONF_SENSOR_SID, "")
            label = s.get(CONF_SENSOR_NAME) or sid
            cls = s.get(CONF_SENSOR_CLASS, "")
            choices[sid] = f"{label}  ({cls}  —  {sid})"

        return self.async_show_form(
            step_id="remove_configured_device",
            data_schema=vol.Schema({
                vol.Required(CONF_SENSOR_SID): vol.In(choices),
            }),
        )

    # ----------------------------------------------------------------- helpers

    def _get_discovered_devices(self):
        entry_data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
        discovered = dict(entry_data.get(ENTRY_DATA_DISCOVERED, {}))
        configured_sids = {
            cfg.get(CONF_SENSOR_SID)
            for cfg in get_configured_sensors(self.config_entry)
        }
        return {
            sid: info
            for sid, info in discovered.items()
            if sid not in configured_sids
        }
