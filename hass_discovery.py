import json
import paho.mqtt.client as mqtt
from hass_entities import entities


def discovery(client: mqtt.Client, zones, zone_timers) -> None:
    payload_common = {
        "state_topic": "home/alarm_test",
        "enabled_by_default": True,
        "availability": {
            "topic": "home/alarm_test/availability"
        },
        "device": {
            "name": "RPi security alarm",
            "identifiers": 202146225,
            "model": "Raspberry Pi security alarm",
            "manufacturer": "The Cavelab"
        }
    }

    for entity in entities:
        payload = payload_common | {
            "name": entity.label,
            "unique_id": "rpi_alarm_" + entity.id
        }

        if entity.data_key is not None:
            payload = payload | {
                "value_template": "{{ value_json." + entity.data_key + " }}"
            }

        if entity.component == "binary_sensor":
            payload = payload | {
                    "payload_off": False,
                    "payload_on": True
                    }

        if entity.component == "switch":
            payload = payload | {
                    "payload_off": json.dumps({"option": entity.id, "value": False}),
                    "payload_on": json.dumps({"option": entity.id, "value": True}),
                    "state_off": False,
                    "state_on": True,
                    "command_topic": "home/alarm_test/config"
                    }

        if entity.component == "button":
            payload = payload | {
                    "payload_press": json.dumps({"option": entity.id, "value": True}),
                    "command_topic": "home/alarm_test/action"
                    }

        if entity.dev_class is not None:
            payload = payload | {
                    "device_class": entity.dev_class
                    }

        if entity.state_class is not None:
            payload = payload | {
                    "state_class": entity.state_class
                    }

        if entity.category is not None:
            payload = payload | {
                    "entity_category": entity.category
                    }

        if entity.icon is not None:
            payload = payload | {
                    "icon": "mdi:" + entity.icon
                    }

        if entity.unit is not None:
            payload = payload | {
                    "unit_of_measurement": entity.unit
                    }

        client.publish(f'homeassistant/{entity.component}/rpi_alarm/{entity.id}/config',
                       json.dumps(payload), retain=True)

    for key, zone in zones.items():
        if zone.dev_class.value is None:
            continue
        payload = payload_common | {
            "name": zone.label,
            "unique_id": "rpi_alarm_" + key,
            "device_class": zone.dev_class.value,
            "value_template": "{{ value_json.zones." + key + " }}",
            "payload_off": False,
            "payload_on": True,
        }

        client.publish(f'homeassistant/binary_sensor/rpi_alarm/{key}/config',
                       json.dumps(payload), retain=True)

    for key, timer in zone_timers.items():
        payload_binary_sensor = payload_common | {
            "name": timer.label + " timer",
            "unique_id": "rpi_alarm_timer_" + key,
            "value_template": "{{ value_json.zone_timers." + key + ".value }}",
            "json_attributes_topic": "home/alarm_test",
            "json_attributes_template": "{{ value_json.zone_timers." + key + ".attributes | tojson }}",
            "payload_off": False,
            "payload_on": True,
            "icon": "mdi:timer"
        }
        client.publish(f'homeassistant/binary_sensor/rpi_alarm/timer_{key}/config',
                       json.dumps(payload_binary_sensor), retain=True)

        payload_button = payload_common | {
            "name": timer.label + " timer cancel",
            "unique_id": "rpi_alarm_timer_cancel_" + key,
            "payload_press": json.dumps({"option": "zone_timer_cancel", "value": key}),
            "command_topic": "home/alarm_test/action",
            "icon": "mdi:timer-cancel"
        }
        client.publish(f'homeassistant/button/rpi_alarm/timer_cancel_{key}/config',
                       json.dumps(payload_button), retain=True)

    alarm_control_panel = payload_common | {
        "name": "Panel",
        "unique_id": "rpi_alarm_panel",
        "value_template": "{{ value_json.state }}",
        "command_topic": "home/alarm_test/set",
        "code": "REMOTE_CODE",
        "command_template": "{ \"action\": \"{{ action }}\", \"code\": \"{{ code }}\" }"
    }

    client.publish(f'homeassistant/alarm_control_panel/rpi_alarm/alarm_panel/config',
                   json.dumps(alarm_control_panel), retain=True)
