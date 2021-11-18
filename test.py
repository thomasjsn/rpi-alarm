import paho.mqtt.client as mqtt
import time
import json
import threading
import logging
import RPi.GPIO as GPIO
import datetime

GPIO.setmode(GPIO.BCM)   # set board mode to Broadcom
GPIO.setwarnings(False)  # don't show warnings

inputs = {
    "tamper": 14
}

outputs = {
    "led_green": 15,
    "led_red": 18
}

for c, v in inputs.items():
    GPIO.setup(v, GPIO.IN)

for c, v in outputs.items():
    GPIO.setup(v, GPIO.OUT)
    GPIO.output(v, False)

class Payload:

    def __init__(self):
        self.data = {
            "state": "disarmed",
            "tamper": False,
            "zones": {
                1: False,
                2: False,
                3: False,
                4: False
            },
            "triggered": {
                "zone": None,
                "timestamp": None
            },
            "armed": {
                "by": None,
                "timestamp": None
            },
            "disarmed": {
                "by": None,
                "timestamp": None
            }
        }
        self._lock = threading.Lock()

    def json(self):
        return json.dumps(self.data)

    def publish(self):
        client.publish('home/alarm_test', self.json())

    def set_state(self, state):
        with self._lock:
            logging.info("State changed to: " + state)
            self.data["state"] = state
            self.publish()

    def triggered(self, zone):
        with self._lock:
            self.data["triggered"]["zone"] = zone
            self.data["triggered"]["timestamp"] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.set_state("triggered")


def buzzer(i, x, state):
    for _ in range(i):
        GPIO.output(outputs["led_red"], 1)
        time.sleep(x[0])
        GPIO.output(outputs["led_red"], 0)
        time.sleep(x[1])
        if payload.data["state"] != state:
            return False
    return True

def arming():
    payload.set_state("arming")
    if buzzer(10, [0.1, 0.9], "arming") is True:
        payload.set_state("armed_away")

def pending(state, zone):
    logging.warning("Pending!")
    payload.set_state("pending")
    if buzzer(10, [0.5, 0.5], "pending") is True:
        triggered(state, zone)

def triggered(state, zone):
    logging.warning("Triggered!")
    payload.triggered(zone)
    if buzzer(10, [0.8, 0.2], "triggered") is True:
        payload.set_state(state)

def run_led():
    while True:
        if payload.data["state"] == "disarmed":
            time.sleep(3)
        else:
            time.sleep(0.5)
        GPIO.output(outputs["led_green"], 1)
        time.sleep(0.1)
        GPIO.output(outputs["led_green"], 0)

def check(zone, delayed):
    if payload.data["state"] == "armed_away":
        if delayed:
            x = threading.Thread(target=pending, args=("armed_away",zone,))
            x.start()
        else:
            x = threading.Thread(target=triggered, args=("armed_away",zone,))
            x.start()


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    logging.info("Connected with result code " + str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("home/alarm_test/set")
    client.subscribe("zigbee2mqtt/Alarm panel")
    client.subscribe("zigbee2mqtt/Door front")
    client.subscribe("zigbee2mqtt/Motion 2nd floor")
    client.subscribe("zigbee2mqtt/Motion kitchen")

    if rc==0:
        client.connected_flag=True
        client.publish("home/alarm_test/availability", "online")
    else:
        client.bad_connection_flag=True

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    logging.debug("Received message: " + msg.topic + " " + str(msg.payload.decode('utf-8')))

    y = json.loads(str(msg.payload.decode('utf-8')))

    if msg.topic == "home/alarm_test/set":
        action = y["action"]
        code = y.get("code")
        logging.info("Action requested: " + action)

        if action == "DISARM" and code == "1234":
            payload.set_state("disarmed")

        if action == "ARM_AWAY" and code == "1234":
            x = threading.Thread(target=arming, args=())
            x.start()

        if action == "ARM_HOME" and code == "1234":
            payload.set_state("armed_home")

    if msg.topic == "zigbee2mqtt/Alarm panel":
        action = y["action"]
        code = y.get("action_code")
        logging.info("Action requested: " + str(action or "none"))

        if action == "disarm" and code == "1234":
            payload.set_state("disarmed")

        if action == "arm_all_zones" and code == "1234":
            x = threading.Thread(target=arming, args=())
            x.start()

        if action == "arm_day_zones" and code == "1234":
            payload.set_state("armed_home")

        if y["tamper"] == True:
            check(0, False)

    if msg.topic == "zigbee2mqtt/Door front":
        if y["contact"] == False:
            check(1, True)

    if msg.topic == "zigbee2mqtt/Motion kitchen":
       if y["occupancy"] == True:
           check(2, False)

    if msg.topic == "zigbee2mqtt/Motion 2nd floor":
       if y["occupancy"] == True:
           check(3, False)


format = "%(asctime)s - %(levelname)s: %(message)s"
logging.basicConfig(format=format, level=logging.DEBUG,datefmt="%H:%M:%S")

client = mqtt.Client('alarm-test')
client.on_connect = on_connect
client.on_message = on_message
client.will_set("home/alarm_test/availability", "offline")
client.connect("mqtt.lan.uctrl.net")
client.loop_start()

payload = Payload()
payload.publish()

if __name__ == "__main__":
    run_led = threading.Thread(target=run_led, args=())
    run_led.start()

    while True:
        time.sleep(0.01)

        #print(json.dumps(payload.data, indent=4, sort_keys=True))

        if GPIO.input(inputs["tamper"]) == 1:
            check(0, True)
