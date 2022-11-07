import time
import json
import threading
import logging
import datetime
import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import configparser
import argparse
import atexit
import os

from pushover import Pushover
import hass
import healthchecks
from arduino import Arduino

GPIO.setmode(GPIO.BCM)   # set board mode to Broadcom
GPIO.setwarnings(False)  # don't show warnings

config = configparser.ConfigParser()
config.read('config.ini')

parser = argparse.ArgumentParser()
parser.add_argument('--silent', dest='silent', action='store_true', help="suppress siren outputs")
parser.add_argument('--payload', dest='print_payload', action='store_true', help="print payload on publish")
parser.add_argument('--siren-test', dest='siren_test', action='store_true', help="sirens test (loud)")
parser.add_argument('--walk-test', dest='walk_test', action='store_true', help="signal when zones trigger")
#parser.set_defaults(feature=True)
args = parser.parse_args()



class Input:
    def __init__(self, gpio, label=None, dev_class=None, delay=False):
        self.gpio = gpio
        self.label = label or f"Input {self.gpio}"
        self.dev_class = dev_class
        self.delay = delay

    def __str__(self):
        return self.label

    def __repr__(self):
        return f"i{self.gpio}:{self.label}"

    def get(self):
        return GPIO.input(self.gpio) == 1

    @property
    def is_true(self):
        return self.get()


class Output:
    def __init__(self, gpio, label=None, debug=False):
        self.gpio = gpio
        self.label = label or f"Output {self.gpio}"
        self.debug = debug

    def __str__(self):
        return self.label

    def set(self, state):
        if self.get() != state:
            if self in [outputs["siren1"], outputs["siren2"]] and args.silent and state:
                logging.debug("Supressing %s, because silent", self)
                return

            GPIO.output(self.gpio, state)
            if self.debug:
                logging.debug("Output: %s set to %s", self, state)

    def get(self):
        return GPIO.input(self.gpio) == 1

    @property
    def is_true(self):
        return self.get()


class Sensor:
    def __init__(self, topic, field, value, label=None, delay=False, timeout=0, dev_class=None):
        self.topic = topic
        self.field = field
        self.value = value
        self.label = label
        self.delay = delay
        self.timeout = timeout
        self.timestamp = time.time()
        self.dev_class = dev_class

    def __str__(self):
        return self.label

    def __repr__(self):
        return f"s:{self.label}"


class Entity:
    def __init__(self, field, component, label=None, dev_class=None, unit=None, category=None):
        self.field = field
        self.component = component
        self.label = label
        self.dev_class = dev_class
        self.unit = unit
        self.category = category

    def __str__(self):
        return self.label


class ZoneTimer:
    def __init__(self, zones, zone_value, seconds, label=None):
        self.zones = zones
        self.zone_value = zone_value
        self.seconds = seconds
        self.label = label
        self.timestamp = time.time()

    def __str__(self):
        return self.label


inputs = {
    "ext_tamper": Input(
        gpio=2,
        label="External tamper",
        dev_class="tamper"
        ),
    "zone01": Input(3, "1st floor hallway", "motion"),
    #"zone02": Input(4),
    #"zone03": Input(17),
    #"zone04": Input(27),
    #"zone05": Input(14),
    #"zone06": Input(15),
    #"zone07": Input(18),
    #"zone08": Input(22),
    #"zone09": Input(23),
    #"1st_floor_tamper": Input(24, "1st floor tamper", "tamper")
    }

outputs = {
    "led_red": Output(
        gpio=5,
        label="Red LED"
        ),
    "led_green": Output(
        gpio=6,
        label="Green LED"
        ),
    "buzzer": Output(
        gpio=16,
        label="Buzzer"
        ),
    "siren1": Output(
        gpio=19,
        label="Siren indoor",
        debug=True
        ),
    "siren2": Output(
        gpio=26,
        label="Siren outdoor",
        debug=True
        ),
    #"aux1": Output(13),
    #"aux2": Output(20),
    #"aux3": Output(21)
    }

sensors = {
    "door1": Sensor(
        topic="zigbee2mqtt/Door front",
        field="contact",
        value=False,
        label="Front door",
        delay=True,
        dev_class="door",
        timeout=3900
        ),
    "door2": Sensor(
        topic="zigbee2mqtt/Door back",
        field="contact",
        value=False,
        label="Back door",
        dev_class="door",
        timeout=3900
        ),
    "door3": Sensor(
        topic="zigbee2mqtt/Door 2nd floor",
        field="contact",
        value=False,
        label="2nd floor door",
        dev_class="door",
        timeout=3900
        ),
    "motion1": Sensor(
        topic="zigbee2mqtt/Motion kitchen",
        field="occupancy",
        value=True,
        label="Kitchen",
        timeout=3900,
        dev_class="motion"
        ),
    "motion2": Sensor(
        topic="zigbee2mqtt/Motion 2nd floor",
        field="occupancy",
        value=True,
        label="2nd floor",
        timeout=3900,
        dev_class="motion"
        ),
    "water_leak1": Sensor(
        topic="zigbee2mqtt/Water leak kitchen",
        field="water_leak",
        value=True,
        label="Kitchen water leak",
        timeout=3600,
        dev_class="moisture"
        ),
    "panel_tamper": Sensor(
        topic="zigbee2mqtt/Alarm panel",
        field="tamper",
        value=True,
        label="Panel tamper",
        timeout=2100,
        dev_class="tamper"
        ),
    "panic": Sensor(
        topic="zigbee2mqtt/Alarm panel",
        field="action",
        value="panic",
        label="Panic button"
        ),
    "emergency": Sensor(
        topic="zigbee2mqtt/Alarm panel",
        field="action",
        value="emergency",
        label="Emergency button"
        )
    }

sensors["door1"].battery = Sensor(
        topic="zigbee2mqtt/Door front",
        field="battery",
        value=20
        )
sensors["door2"].battery = Sensor(
        topic="zigbee2mqtt/Door back",
        field="battery",
        value=20
        )
sensors["door3"].battery = Sensor(
        topic="zigbee2mqtt/Door 2nd floor",
        field="battery",
        value=20
        )
sensors["motion2"].battery = Sensor(
        topic="zigbee2mqtt/Motion 2nd floor",
        field="battery",
        value=20
        )
sensors["panel_tamper"].battery = Sensor(
        topic="zigbee2mqtt/Alarm panel",
        field="battery_low",
        value=True
        )
sensors["water_leak1"].battery = Sensor(
        topic="zigbee2mqtt/Water leak kitchen",
        field="battery",
        value=20
        )

#sensors["door1"].status = Sensor(
#        topic="zwave/Front_door/status",
#        field="status",
#        value="Awake"
#        )
#sensors["door2"].status = Sensor(
#        topic="zwave/Back_door/status",
#        field="status",
#        value="Awake"
#        )

zones = inputs | sensors

codes = dict(config["codes"])

entities = {
    "triggered_zone": Entity(
        field="triggered.zone",
        component="sensor",
        label="Triggered zone"
        ),
    "safe_to_arm": Entity(
        field="arm_not_ready",
        component="binary_sensor",
        dev_class="safety",
        label="Ready to arm"
        ),
    "system_fault": Entity(
        field="fault",
        component="binary_sensor",
        dev_class="problem",
        label="System status",
        category="diagnostic"
        ),
    "system_tamper": Entity(
        field="tamper",
        component="binary_sensor",
        dev_class="tamper",
        label="System tamper"
        ),
    "system_temperature": Entity(
        field="temperature",
        component="sensor",
        dev_class="temperature",
        unit="°C",
        label="System temperature",
        category="diagnostic"
        ),
    "system_voltage": Entity(
        field="battery_v",
        component="sensor",
        dev_class="voltage",
        unit="V",
        label="System voltage",
        category="diagnostic"
        ),
    "battery_low": Entity(
        field="battery_low",
        component="binary_sensor",
        dev_class="battery",
        label="Battery low",
        category="diagnostic"
        ),
    "walk_test": Entity(
        field="config.walk_test",
        component="switch",
        label="Walk test",
        category="config"
        ),
    "mains_power": Entity(
        field="status.mains_power_ok",
        component="binary_sensor",
        dev_class="power",
        label="Mains power",
        category="diagnostic"
        ),
    "zigbee_bridge": Entity(
        field="status.zigbee_bridge",
        component="binary_sensor",
        dev_class="connectivity",
        label="Zigbee bridge",
        category="diagnostic"
        )
    }

zone_timers = {
    "hallway_motion": ZoneTimer(
        zones=[
            zones["zone01"],
            zones["motion2"]
        ],
        zone_value=True,
        seconds=30,
        label="Hallway motion"
    ),
    "kitchen_motion": ZoneTimer(
        zones=[
            zones["motion1"]
        ],
        zone_value=True,
        seconds=3600,
        label="Kitchen motion"
    )
}

format = "%(asctime)s - %(levelname)s: %(message)s"
logging.basicConfig(format=format, level=logging.DEBUG, datefmt="%H:%M:%S")

for input in inputs.values():
    GPIO.setup(input.gpio, GPIO.IN)

for output in outputs.values():
    GPIO.setup(output.gpio, GPIO.OUT)
    output.set(False)


def wrapping_up():
    for output in outputs.values():
        output.set(False)

    logging.info("All outputs set to False")

atexit.register(wrapping_up)

class State:
    def __init__(self):
        self.data = {
            "state": config["system"]["state"],
            "clear": None,
            "fault": None,
            "tamper": None,
            "zones": {},
            "triggered": {
                "zone": None,
                "timestamp": None
            },
            "status": {},
            "zone_timers": {},
            "code_attempts": 0,
            "config": {
                "walk_test": False
            }
        }
        self._lock = threading.Lock()
        self._faults = ["mqtt_connected"]
        self.blocked = set()

    def json(self):
        return json.dumps(self.data)

    def publish(self):
        client.publish("home/alarm_test/availability", "online", retain=True)
        client.publish('home/alarm_test', self.json(), retain=True)

        if args.print_payload:
            print(json.dumps(self.data, indent=4, sort_keys=True))

    @property
    def system(self):
        return self.data["state"]

    @system.setter
    def system(self, state):
        with self._lock:
            logging.warning("System state changed to: %s", state)
            self.data["state"] = state
            self.publish()

            if state in ["disarmed", "armed_home", "armed_away"]:
                with open('config.ini', 'w') as configfile:
                    config["system"]["state"] = state
                    config.write(configfile)

    def triggered(self, zone):
        with self._lock:
            self.data["triggered"]["zone"] = str(zone)
            self.data["triggered"]["timestamp"] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.system = "triggered"

    def zone(self, zone_key, value):
        zone = zones[zone_key]
        #clear = not any(self.data["zones"].values())
        #clear = True

        #if self.data["clear"] is not clear:
        #    self.data["clear"] = clear
        #    self.data["clear_to_arm"] = clear
        #    logging.info("All zones are clear: %s", self.data['clear'])

        if self.data["zones"][zone_key] != value:
            self.data["zones"][zone_key] = value
            logging.info("Zone: %s changed to %s", zone, value)

            for timer in zone_timers.values():
                if zone in timer.zones and value == timer.zone_value:
                    #print("FOUND IN TIMER!")
                    timer.timestamp = time.time()

            if value and (args.walk_test or state.data["config"]["walk_test"]):
                buzzer(2, [0.2, 0.2], "disarmed")

            tamper_zones = {k: v for k, v in self.data["zones"].items() if k.endswith('tamper')}
            state.data["tamper"] = any(tamper_zones.values())

            for tamper_key, tamper_status in tamper_zones.items():
                state.data["status"][f"{tamper_key}_ok"] = not tamper_status

            clear = not any(self.data["zones"].values())
            self.data["clear"] = clear
            self.data["arm_not_ready"] = not clear

            self.publish()

        if zone in self.blocked and value is False:
            self.blocked.remove(zone)
            logging.debug("Blocked zones: %s", self.blocked)

    def fault(self):
        faults = [k for k, v in self.data["status"].items() if not v]

        if self._faults != faults:
            self.data["fault"] = bool(faults)
            self._faults = faults
            self.publish()

            if faults:
                faulted_status = ", ".join(faults).upper()
                logging.error("System check failed: %s", faulted_status)
                pushover.push(f"System check failed: {faulted_status}")
            else:
                logging.info("System status restored")
                pushover.push("System status restored")

    def zone_timer(self, timer_key):
        timer = zone_timers[timer_key]
        last_msg_s = round(time.time() - timer.timestamp)
        value = last_msg_s < timer.seconds

        if self.data["zone_timers"][timer_key] != value:
            self.data["zone_timers"][timer_key] = value
            logging.info("Zone timer: %s changed to %s", timer, value)
            self.publish()


def buzzer(i, x, current_state):
    logging.info("Buzzer loop started (%d, %s)", i, x)

    for _ in range(i):
        outputs["buzzer"].set(True)
        time.sleep(x[0])
        outputs["buzzer"].set(False)
        time.sleep(x[1])

        if state.system != current_state:
            logging.info("Buzzer loop aborted")
            return False

    logging.info("Buzzer loop completed")
    return True


def siren(i, zone, current_state):
    logging.info("Siren loop started (%d, %s)", i, zone)

    for x in range(i):
        outputs["siren1"].set(True)

        if zone == zones["emergency"]:
            time.sleep(0.1)
            break

        elif zone.label.endswith("water leak"):
            time.sleep(0.1)
            outputs["siren1"].set(False)
            time.sleep(0.9)

        else:
            if i > 5 and x >= (i/3):
                outputs["siren2"].set(True)
            time.sleep(1)

        if state.system != current_state:
            outputs["siren1"].set(False)
            outputs["siren2"].set(False)
            logging.info("Siren loop aborted")
            return False

    outputs["siren1"].set(False)
    outputs["siren2"].set(False)

    logging.info("Siren loop completed")
    return True


def arming(user):
    state.system = "arming"
    arming_time = int(config["times"]["arming"])

    if buzzer(arming_time, [0.1, 0.9], "arming") is True:
        if state.data["clear"]:
            state.system = "armed_away"
            pushover.push(f"System armed away, by {user}")
            state.data["code_attempts"] = 0
        else:
            logging.error("Unable to arm, zones not clear")
            state.system = "disarmed"
            pushover.push("Arming failed, not clear", 1, {"sound": "siren"})
            buzzer(1, [1, 0], "disarmed")


def pending(current_state, zone):
    delay_time = int(config["times"]["delay"])

    with pending_lock:
        state.system = "pending"
        logging.info("Pending because of zone: %s", zone)

        if buzzer(delay_time, [0.5, 0.5], "pending") is True:
            triggered(current_state, zone)


def triggered(current_state, zone):
    trigger_time = int(config["times"]["trigger"])

    with triggered_lock:
        state.triggered(zone)
        logging.info("Triggered because of zone: %s", zone)
        pushover.push(f"Triggered, zone: {zone}", 2)

        state.blocked.add(zone)
        logging.debug("Blocked zones: %s", state.blocked)

        if siren(trigger_time, zone, "triggered") is True:
            state.system = current_state


def disarmed(user):
    state.system = "disarmed"
    pushover.push(f"System disarmed, by {user}")
    buzzer(2, [0.1, 0.1], "disarmed")


def armed_home(user):
    home_zones = [
        state.data["zones"]["door1"],
        state.data["zones"]["door2"],
        state.data["zones"]["door3"],
        state.data["zones"]["ext_tamper"],
        state.data["zones"]["panel_tamper"]
    ]

    if not any(home_zones):
        state.system = "armed_home"
        pushover.push(f"System armed home, by {user}")
        buzzer(1, [0.1, 0.1], "armed_home")
        state.data["code_attempts"] = 0
    else:
        logging.error("Unable to arm, zones not clear")
        state.system = "disarmed"
        pushover.push("Arming failed, not clear", 1, {"sound": "siren"})
        buzzer(1, [1, 0], "disarmed")


def run_led():
    while True:
        run_led = "led_red" if state.data["fault"] else "led_green"

        if state.system == "disarmed":
            time.sleep(1.5)
        else:
            time.sleep(0.5)

        outputs[run_led].set(True)
        time.sleep(0.5)
        outputs[run_led].set(False)


def check(zone, delayed=False):
    if zone in [zones["panic"], zones["emergency"], zones["water_leak1"]]:
        if not triggered_lock.locked():
            threading.Thread(target=triggered, args=(state.system, zone,)).start()

    if zone in state.blocked:
        return

    if state.system in ["armed_away", "pending"]:
        if delayed and not pending_lock.locked():
            threading.Thread(target=pending, args=("armed_away", zone,)).start()
        if not delayed and not triggered_lock.locked():
            threading.Thread(target=triggered, args=("armed_away", zone,)).start()

    if state.system == "armed_home":
        if not triggered_lock.locked():
            if (zone in [zones["door1"], zones["door2"], zones["door3"]]
                    or zone.label.endswith("tamper")):
                threading.Thread(target=triggered, args=("armed_home", zone,)).start()


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    logging.info("Connected with result code %s", rc)

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("home/alarm_test/set")
    client.subscribe("home/alarm_test/config")
    client.subscribe("zigbee2mqtt/bridge/state")
    client.subscribe("homelab/src_status")

    #topics = set([sensor.topic for sensor in sensors.values()])
    topics = set()

    for sensor in sensors.values():
        topics.add(sensor.topic)

        if hasattr(sensor, "battery"):
            topics.add(sensor.battery.topic)
        if hasattr(sensor, "status"):
            topics.add(sensor.status.topic)

    logging.debug("Topics: %s", topics)

    for topic in topics:
        client.subscribe(topic)

    if rc == 0:
        client.connected_flag = True
        state.data["status"]["mqtt_connected"] = True
        hass.discovery(client, entities, inputs, sensors)
    else:
        client.bad_connection_flag = True
        print("Bad connection, returned code: ", str(rc))


def on_disconnect(client, userdata, rc):
    logging.info("Disconnecting reason %s", rc)
    client.connected_flag = False
    state.data["status"]["mqtt_connected"] = False
    client.disconnect_flag = True


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    logging.debug("Received message: %s %s", msg.topic, msg.payload.decode('utf-8'))

    if msg.topic == "zigbee2mqtt/bridge/state":
        state.data["status"]["zigbee_bridge"] = msg.payload.decode('utf-8') == "online"
        return

    y = json.loads(str(msg.payload.decode('utf-8')))

    if msg.topic == "homelab/src_status":
        state.data["status"]["mains_power_ok"] = y["src2"] == "ok"
        return

    if msg.topic == "home/alarm_test/config":
        cfg_option = y["option"]
        cfg_value = y["value"]

        logging.info("Config option: %s changed to %s", cfg_option, cfg_value)
        state.data["config"][cfg_option] = cfg_value
        state.publish()
        return

    if msg.topic == "home/alarm_test/set":
        action = y["action"]
        code = y.get("code")

        if code in codes:
            user = codes[code]
            logging.info("Action requested: %s by %s", action, user)

            if action == "DISARM":
                threading.Thread(target=disarmed, args=(user,)).start()

            if action == "ARM_AWAY":
                threading.Thread(target=arming, args=(user,)).start()

            if action == "ARM_HOME":
                threading.Thread(target=armed_home, args=(user,)).start()

        else:
            state.data["code_attempts"] += 1
            logging.error("Bad code: %s, attempt: %d", code, state.data["code_attempts"])

    if msg.topic == "zigbee2mqtt/Alarm panel":
        action = y["action"]
        code = y.get("action_code")

        if code in codes:
            user = codes[code]
            logging.info("Action requested: %s by %s", action, user)

            if action == "disarm":
                threading.Thread(target=disarmed, args=(user,)).start()

            if action == "arm_all_zones":
                threading.Thread(target=arming, args=(user,)).start()

            if action == "arm_day_zones":
                threading.Thread(target=armed_home, args=(user,)).start()

        elif code is not None:
            state.data["code_attempts"] += 1
            logging.error("Bad code: %s, attempt: %d", code, state.data["code_attempts"])

    for key, sensor in sensors.items():
        if msg.topic == sensor.topic:
            state.zone(key, y[sensor.field] == sensor.value)

            if y[sensor.field] == sensor.value:
                check(sensor, sensor.delay)

            sensor.timestamp = time.time()

        if hasattr(sensor, 'battery'):
            if msg.topic == sensor.battery.topic:
                if type(sensor.battery.value) == int and type(sensor.battery.field) == int:
                    state.data["status"][f"sensor_{key}_battery"] = y[sensor.battery.field] > sensor.battery.value
                elif type(sensor.battery.value) == bool:
                    state.data["status"][f"sensor_{key}_battery"] = y[sensor.battery.field] != sensor.battery.value

        if hasattr(sensor, 'status'):
            if msg.topic == sensor.status.topic and y[sensor.status.field] == sensor.status.value:
                sensor.timestamp = time.time()


def status_check():
    while True:
        for key, sensor in sensors.items():
            if sensor.timeout == 0:
                continue

            last_msg_s = round(time.time() - sensor.timestamp)
            state.data["status"][f"sensor_{key}_alive"] = last_msg_s < sensor.timeout

        state.data["status"]["code_attempts"] = state.data["code_attempts"] < 3

        for key, timer in zone_timers.items():
            state.zone_timer(key)

        state.fault()
        time.sleep(1)


def hc_ping():
    hc_uuid = config["healthchecks"]["uuid"]

    if not hc_uuid:
        logging.debug("Healthchecks UUID not found, aborting ping.")
        return

    logging.info("Starting Healthchecks ping with UUID %s", hc_uuid)

    while True:
        hc_status = healthchecks.ping(hc_uuid)
        state.data["status"]["healthchecks_ok"] = hc_status

        time.sleep(60)

def serial_data():
    while True:
        data = arduino.data

        if data == "":
            time.sleep(1)
            continue

        if args.print_payload:
            print(json.dumps(data, indent=4, sort_keys=True))

        try:
            state.data["temperature"] = float(data["temperature"])
            state.data["battery_v"] = float(data["voltage1"])

            state.data["status"]["cabinet_temp"] = float(data["temperature"]) < 30

        except ValueError:
            logging.error("ValueError on data from Arduino device")

        #state.data["status"]["mains_power_ok"] = data["inputs"][0] is True
        state.data["status"]["siren1_output_ok"] = outputs["siren1"].get() == data["inputs"][1]
        state.data["status"]["siren1_not_blocked"] = data["outputs"][0] is False
        state.data["status"]["siren2_output_ok"] = outputs["siren2"].get() == data["inputs"][2]
        state.data["status"]["siren2_not_blocked"] = data["outputs"][1] is False

        state.data["battery_low"] = False;

        state.publish()

        time.sleep(10)


client = mqtt.Client('alarm-test')
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message
client.will_set("home/alarm_test/availability", "offline")

for attempt in range(5):
    try:
        client.connect(config["mqtt"]["host"])
        client.loop_start()
    except:
        logging.error("Unable to connect MQTT, retry... (%d)", attempt)
        time.sleep(attempt*3)
    else:
        break;
else:
    logging.error("Unable to connect MQTT, giving up!")

state = State()
pushover = Pushover(
        config["pushover"]["token"],
        config["pushover"]["user"]
        )

arduino = Arduino()

for z in zones:
    state.data["zones"][z] = None

for t in zone_timers.keys():
    state.data["zone_timers"][t] = None

pending_lock = threading.Lock()
triggered_lock = threading.Lock()

if __name__ == "__main__":
    run_led = threading.Thread(target=run_led, args=())
    run_led.start()

    threading.Thread(target=status_check, args=()).start()

    threading.Thread(target=hc_ping, args=()).start()

    threading.Thread(target=arduino.get_data, args=()).start()
    threading.Thread(target=serial_data, args=()).start()

    if args.siren_test and state.system == "disarmed":
        with pending_lock:
            buzzer(10, [0.1, 0.9], "disarmed")
        with triggered_lock:
            siren(30, zones["ext_tamper"], "disarmed")

        wrapping_up()
        os._exit(os.EX_OK)

    while True:
        time.sleep(0.01)

        for key, input in inputs.items():
            state.zone(key, input.get())

            if input.is_true:
                check(input, input.delay)

        if (not triggered_lock.locked() and
                (outputs["siren1"].is_true or outputs["siren2"].is_true)):

                logging.fatal("Siren(s) on outside lock!")
                wrapping_up()
                os._exit(os.EX_SOFTWARE)
