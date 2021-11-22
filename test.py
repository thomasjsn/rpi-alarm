import paho.mqtt.client as mqtt
import time
import json
import threading
import logging
import RPi.GPIO as GPIO
import datetime

GPIO.setmode(GPIO.BCM)   # set board mode to Broadcom
GPIO.setwarnings(False)  # don't show warnings

class Input:
    def __init__(self, gpio, label=None, delay=False):
        self.gpio = gpio
        self.label = label
        self.delay = delay

    def __str__(self):
        return self.label

    def get(self):
        return GPIO.input(self.gpio) == 1

    @property
    def is_true(self):
        return self.get()


class Output:
    def __init__(self, gpio):
        self.gpio = gpio

    def set(self, state):
        if self.get != state:
            GPIO.output(self.gpio, state)

    def get(self):
        return GPIO.input(self.gpio) == 1

    @property
    def is_true(self):
        return self.get()


class Sensor:
    def __init__(self, topic, field, value, label=None, delay=False):
        self.topic = topic
        self.field = field
        self.value = value
        self.label = label
        self.delay = delay

    def __str__(self):
        return self.label


inputs = {
    "tamper": Input(5, "Tamper"),
    "zone1": Input(6, "Hallway 1st floor", True),
    #"zone2": Input(13, "Hallway 2st floor"),
    #"zone3": Input(19),
    #"zone4": Input(26)
}

outputs = {
    "led_red": Output(14),
    "led_green": Output(15),
    "buzzer": Output(2),
    "siren1": Output(3),
    "siren2": Output(4)
}

sensors = {
    "door1": Sensor("zigbee2mqtt/Door front", "contact", False, "Front door", True),
    "motion1": Sensor("zigbee2mqtt/Motion kitchen", "occupancy", True, "Kitchen"),
    "motion2": Sensor("zigbee2mqtt/Motion 2nd floor", "occupancy", True, "2nd floor")
}

codes = {
    "1234": "Test"
}

for key, input in inputs.items():
    GPIO.setup(input.gpio, GPIO.IN)

for key, output in outputs.items():
    GPIO.setup(output.gpio, GPIO.OUT)
    output.set(False)

class State:

    def __init__(self):
        self.data = {
            "state": "disarmed",
            "tamper": False,
            "zones": {
                "zone1": False,
                "zone2": False,
                "zone3": False,
                "zone4": False,
                "tamper": False
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

    @property
    def system(self):
        return self.data["state"]

    @system.setter
    def system(self, state):
        with self._lock:
            logging.info("System state changed to: " + state)
            self.data["state"] = state
            self.publish()

    def triggered(self, zone):
        with self._lock:
            self.data["triggered"]["zone"] = str(zone)
            self.data["triggered"]["timestamp"] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.system = "triggered"

    def zone(self, zone, value):
        if self.data["zones"][zone] != value:
            self.data["zones"][zone] = value
            self.publish()
            print(json.dumps(self.data, indent=4, sort_keys=True))

def buzzer(i, x, current_state):
    for _ in range(i):
        outputs["led_red"].set(True)
        time.sleep(x[0])
        outputs["led_red"].set(False)
        time.sleep(x[1])
        if state.system != current_state:
            return False
    return True

def siren(i, type, current_state):
    for x in range(i):
        if type == "burglary":
            outputs["led_red"].set(True)
            outputs["siren1"].set(True)
            print("Burglary: " + str(x))
            if x > (i/3):
                print("Outdoor siren")
                outputs["siren2"].set(True)
            time.sleep(1)
        if state.system != current_state:
            outputs["led_red"].set(False)
            outputs["siren1"].set(False)
            outputs["siren2"].set(False)
            return False
    outputs["led_red"].set(False)
    outputs["siren1"].set(False)
    outputs["siren2"].set(False)
    return True

def arming():
    state.system = "arming"
    if buzzer(10, [0.1, 0.9], "arming") is True:
        state.system = "armed_away"

def pending(current_state, zone):
    state.system = "pending"
    logging.warning("Pending!")
    if buzzer(10, [0.5, 0.5], "pending") is True:
        triggered(current_state, zone)

def triggered(current_state, zone):
    with triggered_lock:
        state.triggered(zone)
        logging.warning("Triggered!")
        if siren(30, "burglary", "triggered") is True:
            state.system = current_state

def run_led():
    while True:
        if state.system == "disarmed":
            time.sleep(3)
        else:
            time.sleep(0.5)
        outputs["led_green"].set(True)
        time.sleep(0.1)
        outputs["led_green"].set(False)

def check(zone, delayed = False):
    if state.system != "triggered" and str(zone) == "Tamper":
        x = threading.Thread(target=triggered, args=(state.system,zone,))
        x.start()

    if state.system == "armed_away":
        if delayed:
            x = threading.Thread(target=pending, args=("armed_away",zone,))
            x.start()
        elif not triggered_lock.locked():
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

        if action == "DISARM" and code in codes:
            state.system = "disarmed"

        if action == "ARM_AWAY" and code in codes:
            x = threading.Thread(target=arming, args=())
            x.start()

        if action == "ARM_HOME" and code in codes:
            state.system = "armed_home"

    if msg.topic == "zigbee2mqtt/Alarm panel":
        action = y["action"]
        code = y.get("action_code")
        logging.info("Action requested: " + str(action or "none"))

        if action == "disarm" and code in codes:
            state.system = "disarmed"

        if action == "arm_all_zones" and code in codes:
            x = threading.Thread(target=arming, args=())
            x.start()

        if action == "arm_day_zones" and code in codes:
            state.system = "armed_home"

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

state = State()
state.publish()

triggered_lock = threading.Lock()

if __name__ == "__main__":
    run_led = threading.Thread(target=run_led, args=())
    run_led.start()

    while True:
        time.sleep(0.01)

        #print(json.dumps(state.data, indent=4, sort_keys=True))

        #if GPIO.input(inputs["tamper"]) == 1 and debounce[0] == 0:
            #debounce[0] = 10
            #check("Tamper", True)

        for z in inputs:
            if inputs[z].is_true:
                check(inputs[z], inputs[z].delay)

            state.zone(z, inputs[z].get())
