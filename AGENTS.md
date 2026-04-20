# AGENTS.md

## Purpose
- `miio_gateway` is a legacy Home Assistant custom integration for local control of `lumi.gateway.mieu01` via a modified on-device `miio_client`; transport is raw UDP to port `54321`, not cloud APIs. See `README.md` and `custom_components/miio_gateway/__init__.py`.
- The integration is YAML-only (`manifest.json` has `config_flow: false`) and expects `miio_gateway:` config in `configuration.yaml`.

## Read these files first
- `README.md` — supported devices, pairing flow, config examples, custom HA event contract.
- `custom_components/miio_gateway/__init__.py` — config schema, gateway thread, UDP protocol parsing, shared entity base class.
- `custom_components/miio_gateway/{binary_sensor.py,sensor.py}` — how child Zigbee devices are registered and how incoming events map to HA state.
- `custom_components/miio_gateway/{light.py,media_player.py,alarm_control_panel.py}` — built-in gateway entities using SID `miio.gateway`.
- `custom_components/miio_gateway/services.yaml` — only exposed service: `miio_gateway.join_zigbee`.

## Architecture and data flow
- `setup()` in `__init__.py` creates one shared `XiaomiGw` object, stores it in `hass.data[DOMAIN]`, stores configured child sensor definitions in `hass.data[CONF_DATA_DOMAIN]`, then loads all 5 platforms with `discovery.load_platform(...)`.
- `XiaomiGw` owns the UDP socket, a background thread, a send queue, and callback lists. All entities talk to the gateway through `send_to_hub()`.
- Incoming UDP payloads are decoded in `_miio_msg_decode()`, normalized in `_parse_received_resps()`, then fanned out to every entity callback as `(model, sid, event, params)`.
- `XiaomiGwDevice` is the common base for every entity. It handles availability, keepalive timestamps, metadata (`voltage`, `link_quality`, `model`), restore-state support, and manual `entity_id` generation.
- Entity updates are push-driven (`should_poll = False`). New behavior usually belongs in `parse_incoming_data()`, not polling code.

## Project-specific conventions
- Child Zigbee devices only exist if their `sid` is listed under `miio_gateway.sensors:` in YAML. Platform setup calls `gateway.append_known_sid(sid)`; otherwise events only appear as warning logs (`Received event from unregistered sensor...`).
- Built-in gateway entities always use SID `miio.gateway` and are created unconditionally in `light.py`, `media_player.py`, `sensor.py`, and `alarm_control_panel.py`.
- Binary sensor actions that are not simple on/off events fire a Home Assistant bus event `miio_gateway.action` with `entity_id` and `event_type`; see `binary_sensor.py` and the automation example in `README.md`.
- `restore: true` is only meaningful for configured child devices inheriting from `XiaomiGwDevice`; state restoration happens in `async_added_to_hass()`.
- Entity IDs are assigned manually in `XiaomiGwDevice.__init__` from SID and class (for example `binary_sensor.lumi_ab01_button`), so avoid changing naming logic unless you intend a breaking entity registry change.

## How to extend safely
- To add a new child device type, first decide whether it behaves like a `binary_sensor` event stream or a numeric `sensor`, then add event/param handling in the relevant platform module and document the expected YAML `class`.
- Preserve `_pre_parse_data()` behavior in `XiaomiGwDevice`; availability changes trigger `update_device_params()`, keepalive updates `_alive`, and `_otc.log` metadata fills voltage/LQI.
- For request/response methods (`get_prop`, etc.), use `send_to_hub(..., callback=...)`; result callbacks are matched by miio request ID inside `XiaomiGw._result_callbacks`.
- If you add a new HA-facing capability, wire both ends: service definitions belong in `services.yaml` plus `hass.services.register(...)`, and event contracts should stay consistent with `README.md` examples.

## Verified workflows for this repo
- There is no test suite, lint config, or packaging manifest in the repo root. Validation is primarily by loading the custom component in Home Assistant and watching logs.
- Typical manual validation is: copy/symlink this repo into `$HA_CONFIG_DIR/custom_components/miio_gateway`, configure `miio_gateway:` in `configuration.yaml`, restart Home Assistant, then verify entity creation and runtime logs.
- Useful log messages to watch for: `Gateway availability changed!`, `Received event from unregistered sensor: ...`, `Received unknown method: ...`, and socket errors from `__init__.py`.
- Pairing new Zigbee devices is done through the HA service `miio_gateway.join_zigbee`; once paired, use the logged SID in YAML so the device becomes a real entity.

## External dependencies / assumptions
- Requires a modified `miio_client` binary running on the gateway; `README.md` notes this disables Mi Home cloud control.
- Integration depends only on Home Assistant core APIs plus `voluptuous`; `manifest.json` declares no extra Python package requirements.

