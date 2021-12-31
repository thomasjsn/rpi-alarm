import json

def discovery(client, entities, inputs):
    payload_common = {
        "state_topic": "home/alarm_test",
        "enabled_by_default": True,
        "availability": {
            "topic": "home/alarm_test/availability"
        },
        "device": {
            "name": "RPi security alarm",
            "identifiers": 202146225,
            "model": "Raspberry Pi ZeroW security alarm",
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

        if entity.dev_class is not None:
            payload = payload | {
                    "device_class": entity.dev_class
                    }

        #print(json.dumps(payload, indent=4, sort_keys=True))
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

        #print(json.dumps(payload, indent=4, sort_keys=True))
        client.publish(f'homeassistant/binary_sensor/rpi_alarm/{key}/config', json.dumps(payload), retain=True)
