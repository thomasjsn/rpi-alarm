import time
import json
import threading
import logging
import datetime
import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO

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
    def __init__(self, gpio, label=None, debug=False):
        self.gpio = gpio
        self.label = label
        self.debug = debug

    def __str__(self):
        return self.label

    def set(self, state):
        if self.get() != state:
            GPIO.output(self.gpio, state)
            if self.debug:
                logging.debug(f"Output: {self} set to {state}")

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
    #"zone1": Input(6, "Hallway 1st floor", True),
    #"zone2": Input(13, "Hallway 2st floor"),
    #"zone3": Input(19),
    #"zone4": Input(26)
}

outputs = {
    "led_red": Output(14, "Red LED"),
    "led_green": Output(15, "Green LED"),
    "buzzer": Output(2, "Buzzer"),
    "siren1": Output(3, "Siren indoor", True),
    "siren2": Output(4, "Siren outdoor", True)
}

sensors = {
    "door1": Sensor("zigbee2mqtt/Door front", "contact", False, "Front door", True),
    "motion1": Sensor("zigbee2mqtt/Motion kitchen", "occupancy", True, "Kitchen"),
    "motion2": Sensor("zigbee2mqtt/Motion 2nd floor", "occupancy", True, "2nd floor")
}

zones = inputs | sensors

codes = {
    "1234": "Test"
}

format = "%(asctime)s - %(levelname)s: %(message)s"
logging.basicConfig(format=format, level=logging.DEBUG, datefmt="%H:%M:%S")

for key, input in inputs.items():
    GPIO.setup(input.gpio, GPIO.IN)

for key, output in outputs.items():
    GPIO.setup(output.gpio, GPIO.OUT)
    output.set(False)


class State:

    def __init__(self):
        self.data = {
            "state": "disarmed",
            "clear": False,
            "zones": {},
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
        #logging.debug("Published state object")

    @property
    def system(self):
        return self.data["state"]

    @system.setter
    def system(self, state):
        with self._lock:
            logging.warning(f"System state changed to: {state}")
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
            self.data["clear"] = not any(self.data["zones"].values())
            logging.info(f"Zone: {zones[zone]} changed to {value}, clear is {self.data['clear']}")
            self.publish()
            #print(json.dumps(self.data, indent=4, sort_keys=True))


def buzzer(i, x, current_state):
    logging.info(f"Buzzer loop started ({i}, {x})")

    for _ in range(i):
        outputs["led_red"].set(True)
        time.sleep(x[0])
        outputs["led_red"].set(False)
        time.sleep(x[1])

        if state.system != current_state:
            logging.info("Buzzer loop aborted")
            return False

    logging.info("Buzzer loop completed")
    return True


def siren(i, kind, current_state):
    logging.info(f"Siren loop started ({i}, {kind})")

    for x in range(i):
        outputs["led_red"].set(True)
        outputs["siren1"].set(True)

        if x > (i/3) and kind in ["burglary"]:
            outputs["siren2"].set(True)

        if kind == "burglary":
            time.sleep(1)

        if kind == "tamper":
            time.sleep(0.5)
            outputs["led_red"].set(False)
            outputs["siren1"].set(False)
            time.sleep(0.5)

        if state.system != current_state:
            outputs["led_red"].set(False)
            outputs["siren1"].set(False)
            outputs["siren2"].set(False)
            logging.info("Siren loop aborted")
            return False

    outputs["led_red"].set(False)
    outputs["siren1"].set(False)
    outputs["siren2"].set(False)

    logging.info("Siren loop completed")
    return True


def arming():
    state.system = "arming"
    if buzzer(10, [0.1, 0.9], "arming") is True:
        if state.data["clear"]:
            state.system = "armed_away"
        else:
            logging.error("Unable to arm, zones not clear")
            state.system = "disarmed"


def pending(current_state, zone):
    state.system = "pending"
    logging.info(f"Pending because of zone: {zone}")

    if buzzer(10, [0.5, 0.5], "pending") is True:
        triggered(current_state, zone)


def triggered(current_state, zone):
    with triggered_lock:
        state.triggered(zone)
        logging.info(f"Triggered because of zone: {zone}")

        zone_str = "tamper" if str(zone) == "Tamper" else "burglary"
        if siren(30, zone_str, "triggered") is True:
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


def check(zone, delayed=False):
    #if state.system != "triggered" and str(zone) == "Tamper":
    #    x = threading.Thread(target=triggered, args=(state.system,zone,))
    #    x.start()

    if state.system == "armed_away":
        if delayed:
            x = threading.Thread(target=pending, args=("armed_away", zone,))
            x.start()
        elif not triggered_lock.locked():
            x = threading.Thread(target=triggered, args=("armed_away", zone,))
            x.start()

    if state.system == "armed_home":
        if zone in [zones["door1"], zones["tamper"]]:
            x = threading.Thread(target=triggered, args=("armed_home", zone,))
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

    if rc == 0:
        client.connected_flag = True
        client.publish("home/alarm_test/availability", "online")
    else:
        client.bad_connection_flag = True


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

        if y["tamper"] is True:
            check("Panel tamper", False)

    if msg.topic == "zigbee2mqtt/Door front":
        state.zone("door1", y["contact"] is False)
        if y["contact"] is False:
            check(sensors["door1"], True)

    if msg.topic == "zigbee2mqtt/Motion kitchen":
        state.zone("motion1", y["occupancy"] is True)
        if y["occupancy"] is True:
            check(sensors["motion1"], False)

    if msg.topic == "zigbee2mqtt/Motion 2nd floor":
        state.zone("motion2", y["occupancy"] is True)
        if y["occupancy"] is True:
            check(sensors["motion2"], False)


client = mqtt.Client('alarm-test')
client.on_connect = on_connect
client.on_message = on_message
client.will_set("home/alarm_test/availability", "offline")
client.connect("mqtt.lan.uctrl.net")
client.loop_start()

#discover_1 = {
#    "name": "test",
#    "device_class": "motion",
#    "state_topic": "home/alarm_test",
#    "value_template": "{{ value_json.zones.zone1 }}"
#}

#client.publish('homeassistant/binary_sensor/alarm_zone_1/config', json.dumps(discover_1))


state = State()

for z in zones:
    state.data["zones"][z] = False

state.publish()

triggered_lock = threading.Lock()

if __name__ == "__main__":
    run_led = threading.Thread(target=run_led, args=())
    run_led.start()

    while True:
        time.sleep(0.01)

        for z in inputs:
            state.zone(z, inputs[z].get())

            if inputs[z].is_true:
                check(inputs[z], inputs[z].delay)
