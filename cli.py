import paho.mqtt.publish as publish
import configparser
import argparse
import json

config = configparser.ConfigParser()
config.read('config.ini')

parser = argparse.ArgumentParser()
todo_cmd = parser.add_mutually_exclusive_group(required=True)
todo_cmd.add_argument('--action', dest='user_action', action='store',
                      choices=["battery_test"],
                      help="Trigger action")
args = parser.parse_args()

if __name__ == "__main__":
    mqtt_host = config.get("mqtt", "host")
    mqtt_payload = None

    if args.user_action:
        if args.user_action == "battery_test":
            mqtt_payload = json.dumps({"option": "battery_test", "value": True})

        publish.single("home/alarm_test/action", mqtt_payload, hostname=mqtt_host)
