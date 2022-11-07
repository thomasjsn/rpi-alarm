import json

def discovery(client, entities, inputs, sensors):
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

    for key, entity in entities.items():
        payload = payload_common | {
            "name": "RPi security alarm " + entity.label.lower(),
            "unique_id": "rpi_alarm_" + key,
            "value_template": "{{ value_json." + entity.field + " }}"
        }

        if entity.component == "binary_sensor":
            payload = payload | {
                    "payload_off": False,
                    "payload_on": True
                    }

        if entity.component == "switch":
            payload = payload | {
                    "payload_off": json.dumps({"option": key, "value": False}),
                    "payload_on": json.dumps({"option": key, "value": True}),
                    "state_off": False,
                    "state_on": True,
                    "command_topic": "home/alarm_test/config"
                    }

        if entity.dev_class is not None:
            payload = payload | {
                    "device_class": entity.dev_class
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

        client.publish(f'homeassistant/{entity.component}/rpi_alarm/{key}/config', json.dumps(payload), retain=True)

    for key, input in inputs.items():
        payload = payload_common | {
            "name": "RPi security alarm " + input.label.lower(),
            "unique_id": "rpi_alarm_" + key,
            "device_class": input.dev_class,
            "value_template": "{{ value_json.zones." + key + " }}",
            "payload_off": False,
            "payload_on": True,
        }

        client.publish(f'homeassistant/binary_sensor/rpi_alarm/{key}/config', json.dumps(payload), retain=True)

    for key, sensor in sensors.items():
        if sensor.dev_class is None:
            continue
        payload = payload_common | {
            "name": "RPi security alarm " + sensor.label.lower(),
            "unique_id": "rpi_alarm_" + key,
            "device_class": sensor.dev_class,
            "value_template": "{{ value_json.zones." + key + " }}",
            "payload_off": False,
            "payload_on": True,
        }

        client.publish(f'homeassistant/binary_sensor/rpi_alarm/{key}/config', json.dumps(payload), retain=True)
