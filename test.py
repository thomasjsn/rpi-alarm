import paho.mqtt.client as mqtt
import time
import json
import threading
import logging

class Payload:

    def __init__(self):
        self.data = {
            "state": None,
            "tamper": False,
            "zones": {
                1: False,
                2: False,
                3: False,
                4: False
            },
            "triggered": {
                "zone": "Hallway",
                "timestamp": "2021-11-17 09:28"
            },
            "armed": {
                "by": "Thomas",
                "timestamp": "2021-11-17 09:28"
            },
            "disarmed": {
                "by": "Thomas",
                "timestamp": "2021-11-17 09:28"
            }
        }
        self._lock = threading.Lock()

    def json(self):
        return json.dumps(self.data)

    def publish(self):
        client.publish('home/alarm_test', self.json())


def buzzer(i, state):
    for _ in range(i):
        logging.debug("bzzz")
        time.sleep(1)
        if payload.data["state"] != state:
            return False
    logging.debug("done")
    return True

def arming():
    with payload._lock:
        payload.data["state"] = "arming"
        payload.publish()
    if buzzer(20, "arming") is True:
        payload.data["state"] = "armed_away"
        payload.publish()

def pending(state):
    logging.warning("Pending!")
    with payload._lock:
        payload.data["state"] = "pending"
        payload.publish()
    if buzzer(20, "pending") is True:
        triggered(state)

def triggered(state):
    logging.warning("Triggered!")
    with payload._lock:
        payload.data["state"] = "triggered"
        payload.publish()
    if buzzer(30, "triggered") is True:
        payload.data["state"] = state
        payload.publish()
        logging.info("Back to " + state)


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    logging.info("Connected with result code " + str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("home/alarm_test/set")

    if rc==0:
        client.connected_flag=True
        client.publish("home/alarm_test/availability", "online")
    else:
        client.bad_connection_flag=True

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    logging.info("Received message: " + msg.topic + " " + str(msg.payload.decode('utf-8')))

    y = json.loads(str(msg.payload.decode('utf-8')))

    action = y["action"]


    if action == "DISARM":
        payload.data["state"] = "disarmed"
        payload.publish()

    if action == "ARM_AWAY":
        x = threading.Thread(target=arming, args=())
        x.start()

    if action == "ARM_HOME":
        payload.data["state"] = "armed_home"
        payload.publish()


    logging.info("Action requested: " + y["action"])

format = "%(asctime)s - %(levelname)s: %(message)s"
logging.basicConfig(format=format, level=logging.DEBUG,datefmt="%H:%M:%S")

client = mqtt.Client('alarm-test')
client.on_connect = on_connect
client.on_message = on_message
client.will_set("home/alarm_test/availability", "offline")
client.connect("mqtt.lan.uctrl.net")
client.loop_start()




payload = Payload()

if __name__ == "__main__":
    while True:
        time.sleep(2.0)
        #client.publish('home/alarm_test', json.dumps(data))

        #print(json.dumps(payload.data, indent=4, sort_keys=True))
        #logging.debug("main thread")
        if payload.data["state"] == "armed_home":
            time.sleep(5)
            x = threading.Thread(target=pending, args=("armed_home",))
            x.start()
