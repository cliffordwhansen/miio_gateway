# AGENTS.md

## Purpose
- `miio_gateway` is a legacy Home Assistant custom integration for local control of `lumi.gateway.mieu01` via a modified on-device `miio_client`; transport is raw UDP to port `54321`, not cloud APIs. See `README.md` and `custom_components/miio_gateway/__init__.py`.
- The integration is now UI-first with `config_flow`, but still supports YAML import from `configuration.yaml`. Gateway host/port live in the config entry; child devices are stored in entry options (`CONF_SENSORS`).

## Read these files first
- `README.md` — current UI setup flow, YAML import notes, pairing flow, autodiscovery, and event contract.
- `custom_components/miio_gateway/__init__.py` — config entry setup/unload, UDP thread, unknown-device discovery, shared entity base class.
- `custom_components/miio_gateway/config_flow.py` — UI config flow, YAML import flow, autodiscovery confirmation flow, and options flow for add/remove child devices.
- `custom_components/miio_gateway/{binary_sensor.py,sensor.py}` — how child Zigbee devices are registered from config entry options and how incoming events map to HA state.
- `custom_components/miio_gateway/{light.py,media_player.py,alarm_control_panel.py}` — built-in gateway entities using SID `miio.gateway`.
- `custom_components/miio_gateway/translations/en.json` — runtime labels for config/options/discovery flows.

## Architecture and data flow
- `async_setup()` registers `miio_gateway.join_zigbee` and imports YAML into a config entry when `miio_gateway:` exists in `configuration.yaml`.
- `async_setup_entry()` creates one shared `XiaomiGw` object per config entry, stores runtime data in `hass.data[DOMAIN][entry.entry_id]`, and forwards all 5 platforms.
- `XiaomiGw` owns the UDP socket, a background thread, a send queue, and callback lists. All entities talk to the gateway through `send_to_hub()`.
- Incoming UDP payloads are decoded in `_miio_msg_decode()`, normalized in `_parse_received_resps()`, then marshaled back onto the HA loop before entity/result callbacks run.
- Unknown child devices are captured in `ENTRY_DATA_DISCOVERED` and can launch `async_step_integration_discovery` instead of only logging warnings.
- `XiaomiGwDevice` is the common base for every entity. It handles availability, keepalive timestamps, metadata (`voltage`, `link_quality`, `model`), restore-state support, manual `entity_id` generation, and `device_info` grouping.
- Entity updates are push-driven (`should_poll = False`). New behavior usually belongs in `parse_incoming_data()`, not polling code.

## Project-specific conventions
- Gateway connection details are owned by the config entry. Child devices come from `get_configured_sensors(entry)`, which prefers entry options over entry data so the options flow can add/remove devices without rewriting the entry data.
- Child Zigbee devices only become real entities once their `sid` is configured in the entry options; unknown devices now appear through discovery/config flows instead of only warnings.
- Built-in gateway entities always use SID `miio.gateway` and are created unconditionally in `light.py`, `media_player.py`, `sensor.py`, and `alarm_control_panel.py`.
- Binary sensor actions that are not simple on/off events fire a Home Assistant bus event `miio_gateway.action` with `entity_id` and `event_type`; see `binary_sensor.py` and the automation example in `README.md`.
- `restore: true` is only meaningful for configured child devices inheriting from `XiaomiGwDevice`; state restoration happens in `async_added_to_hass()`.
- Every configured child SID also gets a diagnostic battery sensor in `sensor.py`, derived from restored/live `voltage` metadata with class-based battery profiles.
- Device registry grouping is per SID: all entities for a child device share one HA device linked via the gateway device. Battery entity names must not be reused as device names.
- Entity IDs are assigned manually in `XiaomiGwDevice.__init__` from SID and class (for example `binary_sensor.lumi_ab01_button`), so avoid changing naming logic unless you intend a breaking entity registry change.

## How to extend safely
- To add a new child device type, first decide whether it behaves like a `binary_sensor` event stream or a numeric `sensor`, then add event/param handling in the relevant platform module and document the expected config-flow / options-flow `class` value.
- Preserve `_pre_parse_data()` behavior in `XiaomiGwDevice`; availability changes trigger `update_device_params()`, keepalive updates `_alive`, and `_otc.log` metadata fills voltage/LQI.
- For request/response methods (`get_prop`, etc.), use `send_to_hub(..., callback=...)`; result callbacks are matched by miio request ID inside `XiaomiGw._result_callbacks`.
- If you add a new HA-facing capability, wire both ends: service definitions belong in `services.yaml` plus service registration in `__init__.py`, and config/options/discovery flows need matching strings in both `strings.json` and `translations/en.json`.

## Verified workflows for this repo
- There is no test suite, lint config, or packaging manifest in the repo root. Validation is primarily by loading the custom component in Home Assistant and watching logs.
- Typical manual validation is: copy/symlink this repo into `$HA_CONFIG_DIR/custom_components/miio_gateway`, add the integration from the UI (or keep YAML for import), restart Home Assistant, then verify devices/entities and discovery flows.
- Useful log messages to watch for: `Gateway availability changed!`, `Received event from unregistered sensor: ...`, `Received unknown method: ...`, and socket errors from `__init__.py`.
- Pairing new Zigbee devices is done through the HA service `miio_gateway.join_zigbee`; once paired, trigger the device, then add it through the integration discovery/options flow.

## External dependencies / assumptions
- Requires a modified `miio_client` binary running on the gateway; `README.md` notes this disables Mi Home cloud control.
- Integration depends only on Home Assistant core APIs plus `voluptuous`; `manifest.json` declares no extra Python package requirements.

