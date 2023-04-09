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
import math
from itertools import chain

from pushover import Pushover
import hass
import healthchecks
from arduino import Arduino
import battery

GPIO.setmode(GPIO.BCM)   # set board mode to Broadcom
GPIO.setwarnings(False)  # don't show warnings

config = configparser.ConfigParser()
config.read('config.ini')

parser = argparse.ArgumentParser()
parser.add_argument('--silent', dest='silent', action='store_true', help="suppress siren outputs")
parser.add_argument('--siren-block', dest='siren_block_relay', action='store_true', help="activate siren block relay")
parser.add_argument('--payload', dest='print_payload', action='store_true', help="print payload on publish")
parser.add_argument('--status', dest='print_status', action='store_true', help="print status object on publish")
parser.add_argument('--serial', dest='print_serial', action='store_true', help="print serial data on receive")
parser.add_argument('--timers', dest='print_timers', action='store_true', help="print timers debug")
parser.add_argument('--log', dest='log_level', action='store', choices=["DEBUG","INFO","WARNING"], help="set log level")
#parser.set_defaults(feature=True)
args = parser.parse_args()



class Input:
    def __init__(self, gpio, label=None, dev_class=None, delay=False, arm_modes=["away"]):
        self.gpio = gpio
        self.label = label or f"Input {self.gpio}"
        self.dev_class = dev_class
        self.delay = delay
        self.arm_modes = arm_modes

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
                logging.debug("Suppressing %s, because silent", self)
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
    def __init__(self, topic, field, value, label=None, delay=False, arm_modes=["away"], timeout=0, dev_class=None):
        self.topic = topic
        self.field = field
        self.value = value
        self.label = label
        self.delay = delay
        self.arm_modes = arm_modes
        self.timeout = timeout
        self.timestamp = time.time()
        self.dev_class = dev_class

    def __str__(self):
        return self.label

    def __repr__(self):
        return f"s:{self.label}"

    def get(self):
        return state.data["zones"][self.key]

    @property
    def is_true(self):
        return self.get()

    def add_attribute(self, attribute, value, topic=None, field=None):
        if topic is None:
            topic = self.topic
        if field is None:
            field = attribute

        new_attribute = Sensor(topic=topic, field=field, value=value)
        setattr(self, attribute, new_attribute)

class Entity:
    def __init__(self, field, component, label=None, dev_class=None, unit=None, category=None, icon=None):
        self.field = field
        self.component = component
        self.label = label
        self.dev_class = dev_class
        self.unit = unit
        self.category = category
        self.icon = icon

    def __str__(self):
        return self.label


class ZoneTimer:
    def __init__(self, zones, zone_value=True, label=None, blocked_state=[]):
        self.zones = zones
        self.zone_value = zone_value
        self.label = label
        self.blocked_state = blocked_state
        self.timestamp = time.time()

    def __str__(self):
        return self.label

    @property
    def seconds(self):
        return config.getint("zone_timers", self.key, fallback=300)

    def cancel(self):
        self.timestamp = time.time() - self.seconds


class AlarmPanel:
    def __init__(self, topic, fields, actions, label=None, set_states={}, timeout=0):
        self.topic = topic
        self.fields = fields
        self.actions = actions
        self.label = label
        self.set_states = set_states
        self.timeout = timeout
        self.timestamp = time.time()

    def __str__(self):
        return self.label

    def __repr__(self):
        return f"p:{self.label}"

    def set(self, state):
        if state not in self.set_states:
            return

        logging.debug("Sending state: %s to alarm panel %s", self.set_states[state], self.label)
        data = {"arm_mode": {"mode": self.set_states[state]}}
        client.publish(f"{self.topic}/set", json.dumps(data), retain=False)


inputs = {
    "ext_tamper": Input(
        gpio=2,
        label="External tamper",
        dev_class="tamper",
        arm_modes=["home","away"]
        ),
    "zone01": Input(
        gpio=3,
        label="1st floor hallway motion",
        dev_class="motion"
        ),
    #"zone02": Input(4),
    #"zone03": Input(17),
    #"zone04": Input(27),
    #"zone05": Input(14),
    #"zone06": Input(15),
    #"zone07": Input(18),
    #"zone08": Input(22),
    #"zone09": Input(23),
    #"1st_floor_tamper": Input(
    #    gpio=24,
    #    label="1st floor tamper",
    #    dev_class="tamper",
    #    arm_modes=[]
    #    ),
    #"zone11": None,
    #"zone12": None,
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
    "beacon": Output(
        gpio=13,
        label="Beacon",
        debug=True
        ),
    #"aux1": Output(20),
    #"aux2": Output(21)
    }

sensors = {
    "door1": Sensor(
        topic="zigbee2mqtt/Door front",
        field="contact",
        value=False,
        label="Front door",
        delay=True,
        arm_modes=["home","away"],
        dev_class="door",
        timeout=3900
        ),
    "door2": Sensor(
        topic="zigbee2mqtt/Door back",
        field="contact",
        value=False,
        label="Back door",
        arm_modes=["home","away"],
        dev_class="door",
        timeout=3900
        ),
    "door3": Sensor(
        topic="zigbee2mqtt/Door 2nd floor",
        field="contact",
        value=False,
        label="2nd floor door",
        arm_modes=["home","away"],
        dev_class="door",
        timeout=3900
        ),
    "motion1": Sensor(
        topic="zigbee2mqtt/Motion kitchen",
        field="occupancy",
        value=True,
        label="Kitchen motion",
        timeout=3900,
        dev_class="motion"
        ),
    "motion2": Sensor(
        topic="zigbee2mqtt/Motion 2nd floor",
        field="occupancy",
        value=True,
        label="2nd floor hallway motion",
        timeout=3900,
        dev_class="motion"
        ),
    "motion3": Sensor(
        topic="hass2mqtt/binary_sensor/entreen_motion/state",
        field="value",
        value="on",
        label="Entrance motion",
        delay=True,
        dev_class="motion"
        ),
    "g_motion1": Sensor(
        topic="hass2mqtt/binary_sensor/garasjen_motion/state",
        field="value",
        value="on",
        label="Garage motion",
        arm_modes=[],
        dev_class="motion"
        ),
    "water_leak1": Sensor(
        topic="zigbee2mqtt/Water leak kitchen",
        field="water_leak",
        value=True,
        label="Kitchen dishwasher leak",
        arm_modes=["water"],
        timeout=3600,
        dev_class="moisture"
        ),
    "panel_tamper": Sensor(
        topic="zigbee2mqtt/Alarm panel",
        field="tamper",
        value=True,
        label="Panel tamper",
        arm_modes=["home","away"],
        dev_class="tamper"
        ),
    "panic": Sensor(
        topic="zigbee2mqtt/Alarm panel",
        field="action",
        value="panic",
        label="Panic button",
        arm_modes=["direct"]
        ),
    "emergency1": Sensor(
        topic="zigbee2mqtt/Alarm panel",
        field="action",
        value="emergency",
        label="Emergency button 1",
        arm_modes=["direct"]
        ),
    "emergency2": Sensor(
        topic="zigbee2mqtt/0x0015bc0043000dd1",
        field="action",
        value="emergency",
        label="Emergency button 2",
        arm_modes=["direct"]
        ),
    "fire_test": Sensor(
        topic="home/alarm_test/test/fire",
        field="value",
        value="on",
        label="Fire test",
        arm_modes=["fire"]
        )
    }

sensors["door1"].add_attribute("battery", 20)
sensors["door2"].add_attribute("battery", 20)
sensors["door3"].add_attribute("battery", 20)
sensors["motion1"].add_attribute("battery", 20)
sensors["motion2"].add_attribute("battery", 20)
sensors["water_leak1"].add_attribute("battery", 20)
#sensors["panel_tamper"].add_attribute("battery", field="battery_low", value=True)

sensors["door1"].add_attribute("linkquality", 20)
sensors["door2"].add_attribute("linkquality", 20)
sensors["door3"].add_attribute("linkquality", 20)
sensors["motion1"].add_attribute("linkquality", 20)
sensors["motion2"].add_attribute("linkquality", 20)
sensors["water_leak1"].add_attribute("linkquality", 20)
#sensors["panel_tamper"].add_attribute("linkquality", 20)

#sensors["door1"].status = Sensor(
#        topic="zwave/Front_door/status",
#        field="status",
#        value="Awake"
#        )

zones = inputs | sensors

codes = dict(config.items("codes"))

valid_states = [
    "disarmed",
    "armed_home",
    "armed_away",
    "triggered",
    "pending",
    "arming"
]

entities = {
    "triggered": Entity(
        field="triggered",
        component="sensor",
        dev_class="enum",
        label="Triggered alarm",
        icon="alarm-bell",
        category="diagnostic"
        ),
    "safe_to_arm": Entity(
        field="arm_not_ready",
        component="binary_sensor",
        dev_class="safety",
        label="Ready to arm",
        category="diagnostic"
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
        label="System tamper",
        category="diagnostic"
        ),
    "system_temperature": Entity(
        field="temperature",
        component="sensor",
        dev_class="temperature",
        unit="°C",
        label="System temperature",
        category="diagnostic"
        ),
    "battery_voltage": Entity(
        field="battery.voltage",
        component="sensor",
        dev_class="voltage",
        unit="V",
        label="Battery voltage",
        category="diagnostic"
        ),
    "battery_level": Entity(
        field="battery.level",
        component="sensor",
        dev_class="battery",
        unit="%",
        label="Battery",
        category="diagnostic"
        ),
    "battery_low": Entity(
        field="battery.low",
        component="binary_sensor",
        dev_class="battery",
        label="Battery low",
        category="diagnostic"
        ),
    "battery_chrg": Entity(
        field="battery.charging",
        component="binary_sensor",
        dev_class="battery_charging",
        label="Battery charging",
        category="diagnostic"
        ),
    "battery_test_running": Entity(
        field="battery.test_running",
        component="binary_sensor",
        dev_class="running",
        label="Battery test",
        category="diagnostic"
        ),
    "auxiliary_voltage": Entity(
        field="auxiliary_voltage",
        component="sensor",
        dev_class="voltage",
        unit="V",
        label="Auxiliary voltage",
        category="diagnostic"
        ),
    "walk_test": Entity(
        field="config.walk_test",
        component="switch",
        label="Walk test",
        icon="walk",
        category="config"
        ),
    "door_open_warning": Entity(
        field="config.door_open_warning",
        component="switch",
        label="Door open warning",
        icon="door-open",
        category="config"
        ),
    "siren_test": Entity(
        field=None,
        component="button",
        label="Siren test",
        icon="bullhorn",
        category="diagnostic"
        ),
    "battery_test": Entity(
        field=None,
        component="button",
        label="Battery test",
        icon="battery-clock",
        category="diagnostic"
        ),
    "water_alarm_test": Entity(
        field=None,
        component="button",
        label="Water alarm test",
        icon="water-alert",
        category="diagnostic"
        ),
    "mains_power": Entity(
        field="mains_power_ok",
        component="binary_sensor",
        dev_class="power",
        label="Mains power",
        category="diagnostic"
        ),
    "zigbee_bridge": Entity(
        field="zigbee_bridge",
        component="binary_sensor",
        dev_class="connectivity",
        label="Zigbee bridge",
        category="diagnostic"
        ),
    "reboot_required": Entity(
        field="reboot_required",
        component="binary_sensor",
        dev_class="update",
        label="Reboot required",
        category="diagnostic"
        )
    }

zone_timers = {
    "hallway_motion": ZoneTimer(
        zones=["zone01","motion2"],
        #zone_value=True,
        label="Hallway motion",
        blocked_state=["armed_away"]
    ),
    "kitchen_motion": ZoneTimer(
        zones=["motion1"],
        #zone_value=True,
        label="Kitchen motion",
        blocked_state=["armed_away","armed_home"]
    )
}

alarm_panels = {
    "home_assistant": AlarmPanel(
        topic="home/alarm_test/set",
        fields={"action":"action", "code":"code"},
        actions={"disarm":"DISARM", "arm_away":"ARM_AWAY", "arm_home":"ARM_HOME"},
        label="Home Assistant"
    ),
    "climax": AlarmPanel(
        topic="zigbee2mqtt/Alarm panel",
        fields={"action":"action", "code":"action_code"},
        actions={"disarm":"disarm", "arm_away":"arm_all_zones", "arm_home":"arm_day_zones"},
        label="Climax",
        timeout=2100
    ),
    "develco": AlarmPanel(
        topic="zigbee2mqtt/0x0015bc0043000dd1",
        fields={"action":"action", "code":"action_code"},
        actions={"disarm":"disarm", "arm_away":"arm_all_zones", "arm_home":"arm_day_zones"},
        label="Develco",
        set_states = {
            "disarmed": "disarm",
            "armed_home": "arm_day_zones",
            "armed_away": "arm_all_zones",
            "triggered": "in_alarm",
            "pending": "entry_delay",
            "arming": "exit_delay"
        },
        timeout=600
    )
}

format = "%(asctime)s - %(levelname)s: %(message)s"
logging.basicConfig(format=format, level=logging.DEBUG, datefmt="%H:%M:%S")

battery_log = logging.getLogger("battery")
battery_log_handler = logging.FileHandler('battery.log')
battery_log_handler.setFormatter(logging.Formatter(format))
battery_log.addHandler(battery_log_handler)

if args.log_level:
    logging.getLogger().setLevel(args.log_level)
    logging.info("Log level set to %s", args.log_level)

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
            "state": config.get("system", "state"),
            "battery": {},
            "fault": None,
            "tamper": None,
            "triggered": None,
            "zones": {},
            "zone_timers": {},
            "config": {
                "walk_test": config.getboolean("config", "walk_test", fallback=False),
                "door_open_warning": config.getboolean("config", "door_open_warning", fallback=True)
            }
        }
        self._lock = threading.Lock()
        self._faults = ["mqtt_connected"]
        self.blocked = set()
        self.status = {}
        self.code_attempts = 0
        self.zones_open = set()
        self.garage_notify = time.time()

    def json(self):
        return json.dumps(self.data)

    def publish(self):
        client.publish("home/alarm_test/availability", "online", retain=True)
        client.publish('home/alarm_test', self.json(), retain=True)

        if args.print_payload:
            print(json.dumps(self.data, indent=4, sort_keys=True))

        if args.print_status:
            print(json.dumps(self.status, indent=4, sort_keys=True))

    @property
    def system(self):
        return self.data["state"]

    @system.setter
    def system(self, state):
        if state not in valid_states:
            raise ValueError(f"State: {state} is not valid")

        with self._lock:
            logging.warning("System state changed to: %s", state)

            #if (state == "armed_away" and self.data["state"] == "triggered") or state == "disarmed":
            if state in ["disarmed", "armed_home", "armed_away"]:
                self.code_attempts = 0
                self.data["triggered"] = None

                if len(self.zones_open) > 0:
                    logging.info("Clearing open zones: %s", self.zones_open)
                    self.zones_open.clear()

            self.data["state"] = state
            self.publish()

            if state in ["disarmed", "armed_home", "armed_away"]:
                with open('config.ini', 'w') as configfile:
                    config.set("system", "state", state)
                    config.write(configfile)

            for panel in [v for k, v in alarm_panels.items() if v.set_states]:
                panel.set(state)


    def zone(self, zone_key, value):
        zone = zones[zone_key]

        if self.data["zones"][zone_key] != value:
            self.data["zones"][zone_key] = value
            logging.info("Zone: %s changed to %s", zone, value)

            for timer_key, timer in zone_timers.items():
                if zone_key in timer.zones:
                    #logging.debug("Zone: %s found in timer %s", zone, timer_key)
                    self.zone_timer(timer_key)

            if value and (state.data["config"]["walk_test"]):
                threading.Thread(target=buzzer_signal, args=(2, [0.2, 0.2])).start()

            if value and self.system in ["triggered", "armed_home", "armed_away"]:
                if zone == zones["g_motion1"] and (time.time() - self.garage_notify > 60):
                    pushover.push(f"Notify zone is open: {zone}", 1)

            #if value and zone.dev_class == "door" and self.system == "disarmed":
            #    threading.Thread(target=buzzer_signal, args=(2, [0.2, 0.2])).start()

            tamper_zones = {k: v.get() for k, v in zones.items() if v.dev_class == 'tamper'}
            state.data["tamper"] = any(tamper_zones.values())

            for tamper_key, tamper_status in tamper_zones.items():
                state.status[f"{tamper_key}_ok"] = not tamper_status

            clear = not any([o.get() for o in away_zones])
            self.data["arm_not_ready"] = not clear

            self.publish()

        if zone in self.blocked and value is False:
            self.blocked.remove(zone)
            logging.debug("Blocked zones: %s", self.blocked)

    def fault(self):
        faults = [k for k, v in self.status.items() if not v]

        if self._faults != faults:
            self.data["fault"] = bool(faults)
            self._faults = faults
            self.publish()

            if faults:
                faulted_status = ", ".join(faults).upper()
                logging.error("System check(s) failed: %s", faulted_status)
                pushover.push(f"System check(s) failed: {faulted_status}")
            else:
                logging.info("System status restored")
                pushover.push("System status restored")

    def zone_timer(self, timer_key):
        timer = zone_timers[timer_key]
        timer_zones = [v for k, v in self.data["zones"].items() if k in timer.zones]
        #print(json.dumps(timer_zones, indent=4, sort_keys=True))

        #if timer.zone_value:
        #    zone_state = any(timer_zones)
        #else:
        #    zone_state = not any(timer_zones)

        zone_state = any(timer_zones)

        if zone_state:
            timer.timestamp = time.time()

        if state.system in timer.blocked_state:
            timer.cancel()

        last_msg_s = round(time.time() - timer.timestamp)
        value = last_msg_s < timer.seconds

        #if not timer.zone_value:
        #    value = not value

        if self.data["zone_timers"][timer_key] != value:
            self.data["zone_timers"][timer_key] = value
            logging.info("Zone timer: %s changed to %s", timer, value)
            self.publish()

        if args.print_timers and value:
            print(f"{timer}: {datetime.timedelta(seconds=timer.seconds-last_msg_s)}")


def buzzer(seconds, current_state):
    logging.info("Buzzer loop started (%d seconds)", seconds)
    start_time = time.time()

    while (start_time + seconds) > time.time():
        if current_state == "arming":
            if any([o.get() for o in home_zones]):
                buzzer_signal(1, [0.2, 0.8])
            else:
                buzzer_signal(1, [0.1, 0.9])

        if current_state == "pending":
            buzzer_signal(1, [0.5, 0.5])

        if state.system != current_state:
            logging.info("Buzzer loop aborted")
            return False

    logging.info("Buzzer loop completed")
    return True


def buzzer_signal(i, x):
    with buzzer_lock:
        for _ in range(i):
            outputs["buzzer"].set(True)
            time.sleep(x[0])
            outputs["buzzer"].set(False)
            time.sleep(x[1])


def siren(seconds, zone, current_state):
    logging.info("Siren loop started (%d seconds, %s, %s)",
                 seconds, zone, current_state)
    start_time = time.time()
    #zones_open = len(state.zones_open)

    while (start_time + seconds) > time.time():
        # ANSI S3.41-1990; Temporal Three or T3 pattern
        # Indoor siren uses about 0.2 seconds to reach
        if zone in fire_zones:
            for _ in range(3):
                outputs["siren1"].set(True)
                time.sleep(0.7)
                outputs["siren1"].set(False)
                time.sleep(0.3)
            time.sleep(1)

        elif zone in [zones["emergency1"], zones["emergency2"]]:
            outputs["siren1"].set(True)
            time.sleep(0.5)
            outputs["siren1"].set(False)
            time.sleep(10)
            break

        elif zone in water_zones:
            outputs["siren1"].set(True)
            time.sleep(0.5)
            outputs["siren1"].set(False)
            time.sleep(10)

        else:
            outputs["siren1"].set(True)
            outputs["beacon"].set(True)

            if (time.time()-start_time) > (seconds/3) and len(state.zones_open) > 1:
                outputs["siren2"].set(True)
            time.sleep(1)

        if state.system != current_state:
            outputs["siren1"].set(False)
            outputs["siren2"].set(False)
            outputs["beacon"].set(False)
            logging.info("Siren loop aborted")

            return False

        #if len(state.zones_open) > zones_open:
        #    logging.warning("Open triggered zones increased, extending trigger time")
        #    logging.debug("Trigger time increased by: %d seconds", time.time() - start_time)
        #    start_time = time.time()
        #    zones_open = len(state.zones_open)

    outputs["siren1"].set(False)
    outputs["siren2"].set(False)
    outputs["beacon"].set(False)
    logging.info("Siren loop completed")

    return True


def arming(user):
    state.system = "arming"
    arming_time = config.getint("times", "arming")

    if args.silent or args.siren_block_relay:
        arming_time = 10

    if buzzer(arming_time, "arming") is True:
        active_away_zones = [o.label for o in away_zones if o.get()]

        if not active_away_zones:
            state.system = "armed_away"
            pushover.push(f"System armed away, by {user}")
        else:
            logging.error("Arm away failed, not clear: %s", active_away_zones)
            state.system = "disarmed"
            active_away_zones_str = ", ".join(active_away_zones)
            pushover.push(f"Arm away failed, not clear: {active_away_zones_str}", 1, {"sound": "siren"})
            buzzer_signal(1, [1, 0])


def pending(current_state, zone):
    delay_time = config.getint("times", "delay")

    if args.silent or args.siren_block_relay:
        delay_time = 10

    with pending_lock:
        state.system = "pending"
        logging.info("Pending because of zone: %s", zone)

        if buzzer(delay_time, "pending") is True:
            triggered(current_state, zone)


def triggered(current_state, zone):
    trigger_time = config.getint("times", "trigger")

    if args.silent or args.siren_block_relay:
        trigger_time = 30

    with triggered_lock:
        if zone in fire_zones:
            state.data["triggered"] = "Fire"
        elif zone in water_zones:
            state.data["triggered"] = "Water leak"
        elif zone in direct_zones:
            state.data["triggered"] = "Emergency"
        else:
            state.data["triggered"] = "Intrusion"

        state.system = "triggered"
        logging.warning("Triggered because of zone: %s", zone)
        pushover.push(f"Triggered, zone: {zone}", 2)

        state.blocked.add(zone)
        logging.debug("Blocked zones: %s", state.blocked)

        if siren(trigger_time, zone, "triggered") is True:
            state.system = current_state


def disarmed(user):
    state.system = "disarmed"
    pushover.push(f"System disarmed, by {user}")
    buzzer_signal(2, [0.1, 0.1])


def armed_home(user):
    active_home_zones = [o.label for o in home_zones if o.get()]

    if not active_home_zones:
        state.system = "armed_home"
        pushover.push(f"System armed home, by {user}")
        buzzer_signal(1, [0.1, 0.1])
    else:
        logging.error("Arm home failed, not clear: %s", active_home_zones)
        state.system = "disarmed"
        active_home_zones_str = ", ".join(active_home_zones)
        pushover.push(f"Arm home failed, not clear: {active_home_zones_str}", 1, {"sound": "siren"})
        buzzer_signal(1, [1, 0])


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


def check(zone):
    if zone in fire_zones or (state.system != "armed_away" and zone in direct_zones):
        if not triggered_lock.locked():
            threading.Thread(target=triggered, args=(state.system, zone,)).start()

    if zone in water_zones:
        if not triggered_lock.locked():
            arduino.commands.put([3, True]) # Water valve relay
            arduino.commands.put([4, True]) # Dishwasher relay (NC)
            threading.Thread(target=triggered, args=(state.system, zone,)).start()

    if zone in state.blocked:
        return

    if state.system in ["armed_away", "pending"] and zone in away_zones:
        if zone.delay and not pending_lock.locked():
            threading.Thread(target=pending, args=("armed_away", zone,)).start()
        if not zone.delay and not triggered_lock.locked():
            threading.Thread(target=triggered, args=("armed_away", zone,)).start()

    if state.system == "armed_home" and zone in home_zones:
        if not triggered_lock.locked():
            threading.Thread(target=triggered, args=("armed_home", zone,)).start()

    if state.system in ["armed_away", "pending", "triggered"] and zone in away_zones:
        zones_open = len(state.zones_open)
        state.zones_open.add(zone)

        if len(state.zones_open) > zones_open:
            logging.info("Added zone to list of open zones: %s", zone)
            if len(state.zones_open) > 1 and state.system == "triggered":
                zones_open_str = ", ".join([o.label for o in state.zones_open])
                pushover.push(f"Multiple triggered zones: {zones_open_str}", 1)


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    logging.info("Connected to MQTT broker with result code %s", rc)

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.

    topics = set()
    topics.add("zigbee2mqtt/bridge/state")

    for option in ["config", "action"]:
        client.subscribe(f"home/alarm_test/{option}")

    for panel in alarm_panels.values():
        topics.add(panel.topic)

    for sensor in sensors.values():
        topics.add(sensor.topic)

        if hasattr(sensor, "battery"):
            topics.add(sensor.battery.topic)
        if hasattr(sensor, "status"):
            topics.add(sensor.status.topic)
        if hasattr(sensor, "linkquality"):
            topics.add(sensor.linkquality.topic)

    logging.debug("Topics: %s", topics)

    for topic in topics:
        client.subscribe(topic)

    if rc == 0:
        client.connected_flag = True
        state.status["mqtt_connected"] = True
        hass.discovery(client, entities, inputs, sensors, zone_timers)
    else:
        client.bad_connection_flag = True
        print("Bad connection, returned code: ", str(rc))


def on_disconnect(client, userdata, rc):
    logging.warning("Disconnecting reason %s", rc)
    client.connected_flag = False
    state.status["mqtt_connected"] = False
    client.disconnect_flag = True


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    logging.debug("Received message: %s %s", msg.topic, msg.payload.decode('utf-8'))

    if msg.payload.decode('utf-8') == "":
        logging.warning("Received empty payload, discarded")
        return

    try:
        y = json.loads(str(msg.payload.decode('utf-8')))
    except json.JSONDecodeError:
        y = {"value": msg.payload.decode('utf-8')}
        logging.debug("Unable to decode JSON, created object %s", y)

    if msg.topic == "zigbee2mqtt/bridge/state" and "value" in y:
        state.status["zigbee_bridge"] = y["value"] == "online"
        state.data["zigbee_bridge"] = state.status["zigbee_bridge"]
        return

    if msg.topic == "home/alarm_test/config" and all(k in y for k in ("option","value")):
        cfg_option = y["option"]
        cfg_value = y["value"]

        with open('config.ini', 'w') as configfile:
            config.set('config', cfg_option, str(cfg_value))
            config.write(configfile)

        logging.info("Config option: %s changed to %s", cfg_option, cfg_value)
        state.data["config"][cfg_option] = cfg_value
        state.publish()
        return

    if msg.topic == "home/alarm_test/action" and all(k in y for k in ("option","value")):
        act_option = y["option"]
        act_value = y["value"]

        logging.info("Action triggered: %s, with value: %s", act_option, act_value)

        if act_option == "siren_test" and act_value:
            #arduino.commands.put([1, True]) # Siren block relay
            with pending_lock:
                buzzer_signal(7, [0.1, 0.9])
                buzzer_signal(1, [2.5, 0.5])
            with triggered_lock:
                siren_test_zones = [v for k, v in zones.items() if v.dev_class == "tamper"]
                if siren_test_zones and len(zones) > 2:
                    state.zones_open.update(list(zones.values())[:2])
                    siren(3, siren_test_zones[0], "disarmed") # use first tamper zone to test
                    #state.zones_open.clear()
                else:
                    logging.error("Not enough zones defined, unable to run siren test!")
            #arduino.commands.put([1, False]) # Siren block relay

        if act_option == "zone_timer_cancel" and act_value in zone_timers:
            timer = zone_timers[act_value]
            timer.cancel()

        if act_option == "battery_test" and act_value:
            if not battery_test_thread.is_alive():
                battery_test_thread.start()
            else:
                logging.error("Battery test already running!")

        if act_option == "water_alarm_test" and act_value:
            with pending_lock:
                buzzer_signal(7, [0.1, 0.9])
                buzzer_signal(1, [2.5, 0.5])
            if water_zones:
                check(water_zones[0]) # use first water sensor to test
            else:
                logging.error("No water zones defined, unable to run water alarm test!")

        return

    for key, panel in alarm_panels.items():
        if msg.topic == panel.topic:
            panel.timestamp = time.time()

        if msg.topic == panel.topic and panel.fields["action"] in y:
            action = y[panel.fields["action"]]
            code = y.get(panel.fields["code"])

            if msg.retain == 1:
                logging.warning("Discarding action: %s, in retained message from alarm panel: %s", action, panel)
                continue

            #if panel.emergency and action == panel.emergency:
            #    logging.warning(f"Emergency from panel: {panel.label}")

            if code in codes:
                user = codes[code]
                logging.info("Panel action, %s: %s by %s", panel, action, user)

                if action == panel.actions["disarm"]:
                    threading.Thread(target=disarmed, args=(user,)).start()

                elif action == panel.actions["arm_away"]:
                    threading.Thread(target=arming, args=(user,)).start()

                elif action == panel.actions["arm_home"]:
                    threading.Thread(target=armed_home, args=(user,)).start()

                else:
                    logging.warning("Unknown action: %s, from alarm panel: %s", action, panel)

            elif code is not None:
                state.code_attempts += 1
                logging.error("Bad code: %s, attempt: %d", code, state.code_attempts)
                buzzer_signal(1, [1, 0])

    for key, sensor in sensors.items():
        if msg.topic == sensor.topic and sensor.field in y:
            sensor.timestamp = time.time()

            state.zone(key, y[sensor.field] == sensor.value)

            if y[sensor.field] == sensor.value:
                if msg.retain == 1 and sensor in chain(direct_zones, fire_zones):
                    logging.warning("Discarding active sensor: %s, in retained message", sensor)
                    continue

                check(sensor)

        if hasattr(sensor, 'battery'):
            if msg.topic == sensor.battery.topic and sensor.battery.field in y:
                if type(sensor.battery.value) == int and type(y[sensor.battery.field]) == int:
                    state.status[f"sensor_{key}_battery"] = y[sensor.battery.field] > sensor.battery.value
                elif type(sensor.battery.value) == bool:
                    state.status[f"sensor_{key}_battery"] = y[sensor.battery.field] != sensor.battery.value
                #print(f"Sensor {key} battery: {y[sensor.battery.field]}")

        if hasattr(sensor, 'status'):
            if msg.topic == sensor.status.topic and sensor.status.field in y:
                if y[sensor.status.field] == sensor.status.value:
                    sensor.timestamp = time.time()

        if hasattr(sensor, 'linkquality'):
            if msg.topic == sensor.linkquality.topic and sensor.linkquality.field in y:
                state.status[f"sensor_{key}_linkquality"] = y[sensor.linkquality.field] > sensor.linkquality.value
                #print(f"Sensor {key} linkquality: {y[sensor.linkquality.field]}")


def status_check():
    while True:
        for key, device in (sensors.items() | alarm_panels.items()):
            if device.timeout == 0:
                continue

            last_msg_s = round(time.time() - device.timestamp)
            state.status[f"device_{key}_alive"] = last_msg_s < device.timeout

        state.status["code_attempts"] = state.code_attempts < 3

        for key, timer in zone_timers.items():
            state.zone_timer(key)

        state.fault()
        time.sleep(1)


def hc_ping():
    hc_uuid = config.get("healthchecks", "uuid")

    if not hc_uuid:
        logging.debug("Healthchecks UUID not found, aborting ping.")
        return

    logging.info("Starting Healthchecks ping with UUID %s", hc_uuid)

    while True:
        hc_status = healthchecks.ping(hc_uuid)
        state.status["healthchecks_ok"] = hc_status

        time.sleep(60)

def serial_data():
    while True:
        data = arduino.data

        if not data:
            time.sleep(1)
            continue

        if args.print_serial:
            print(json.dumps(data, indent=4, sort_keys=True))

        try:
            state.data["temperature"] = data["temperature"]
            state.status["cabinet_temp"] = data["temperature"] < 30

            state.data["battery"]["voltage"] = data["voltage1"]
            state.data["battery"]["level"] = battery.level(data["voltage1"])
            state.data["battery"]["low"] = data["voltage1"] < 12
            state.data["battery"]["charging"] = data["voltage1"] > 13

            state.data["auxiliary_voltage"] = data["voltage2"]
            state.data["mains_power_ok"] = data["voltage2"] > 12

            state.status["battery_voltage"] = data["voltage1"] > 12
            state.status["mains_power_ok"] = data["voltage2"] > 12

        except ValueError:
            logging.error("ValueError on data from Arduino device")

        #state.status["siren1_output_ok"] = outputs["siren1"].get() == data["inputs"][1]
        #state.status["siren2_output_ok"] = outputs["siren2"].get() == data["inputs"][2]
        state.status["sirens_not_blocked"] = data["outputs"][0] is False

        state.data["battery"]["test_running"] = battery_test_thread.is_alive()

        time.sleep(1)

        if round(time.time(), 0) % 10 == 0:
            state.publish()

def door_open_warning():
    door_closed_time = time.time()

    while True:
        # De Morgan's laws:
        #   not (A or B) = (not A) and (not B)
        #   not (A and B) = (not A) or (not B)
        # If door is closed or warning is disabled
        if not (sensors["door1"].is_true and state.data["config"]["door_open_warning"]):
            door_closed_time = time.time()

        seconds_open = math.floor(time.time() - door_closed_time)

        interval = 20
        if seconds_open > 180:
            interval = 1
        elif seconds_open > 150:
            interval = 5
        elif seconds_open > 120:
            interval = 10
        elif seconds_open > 90:
            interval = 15

        if state.system == "disarmed" and seconds_open > 30 and seconds_open % interval == 0:
            buzzer_signal(1, [0.05, 0.95])
        else:
            time.sleep(1)

def battery_test():
    start_time = time.time()
    battery_log.info("Battery test started at %s V", arduino.data["voltage1"])
    arduino.commands.put([2, True]) # Disable charger

    #while arduino.data["voltage1"] > 12:
    for _ in range(10):
        time.sleep(1)

    test_time = round(time.time() - start_time, 0)
    battery_log.info("Battery test completed at %s V, took: %s",
                     arduino.data["voltage1"], datetime.timedelta(seconds=test_time))
    arduino.commands.put([2, False]) # Re-enable charger

def reboot_required():
    while True:
        reboot_required = os.path.isfile("/var/run/reboot-required")
        state.data["reboot_required"] = reboot_required

        if reboot_required:
            logging.warning("Reboot required!")

        time.sleep(60*60)


client = mqtt.Client(config.get("mqtt", "client_id"))
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message
client.will_set("home/alarm_test/availability", "offline")

for attempt in range(5):
    try:
        client.connect(config.get("mqtt", "host"))
        client.loop_start()
    except:
        logging.error("Unable to connect MQTT, retry... (%d)", attempt)
        time.sleep(attempt*3)
    else:
        break
else:
    logging.error("Unable to connect MQTT, giving up!")

state = State()
pushover = Pushover(
        config.get("pushover", "token"),
        config.get("pushover", "user")
        )

arduino = Arduino(logging)

# Temporary turn off relays related to water alarm here,
# until a proper reset is implemented.
for i in range(3, 5):
    arduino.commands.put([i, False])

if args.siren_block_relay:
    arduino.commands.put([1, True]) # Siren block relay
    logging.warning("Sirens blocked, siren block active!")

if args.silent:
    logging.warning("Sirens suppressed, silent mode active!")

for zone_key, zone in zones.items():
    state.data["zones"][zone_key] = None
    zone.key = zone_key

for timer_key, timer in zone_timers.items():
    state.data["zone_timers"][timer_key] = None
    timer.key = timer_key

pending_lock = threading.Lock()
triggered_lock = threading.Lock()
buzzer_lock = threading.Lock()

battery_test_thread = threading.Thread(target=battery_test, args=())

home_zones = [v for k, v in zones.items() if "home" in v.arm_modes]
logging.info("Zones to arm when home: %s", home_zones)

away_zones = [v for k, v in zones.items() if "away" in v.arm_modes]
logging.info("Zones to arm when away: %s", away_zones)

water_zones = [v for k, v in zones.items() if "water" in v.arm_modes]
logging.info("Water alarm zones: %s", water_zones)

direct_zones = [v for k, v in zones.items() if "direct" in v.arm_modes]
logging.info("Direct alarm zones: %s", direct_zones)

fire_zones = [v for k, v in zones.items() if "fire" in v.arm_modes]
logging.info("Fire alarm zones: %s", fire_zones)

passive_zones = [v for k, v in zones.items() if not v.arm_modes]
logging.info("Passive alarm zones: %s", passive_zones)

if __name__ == "__main__":
    run_led = threading.Thread(target=run_led, args=())
    run_led.start()

    threading.Thread(target=status_check, args=()).start()

    threading.Thread(target=hc_ping, args=()).start()

    threading.Thread(target=arduino.get_data, args=()).start()
    threading.Thread(target=serial_data, args=()).start()

    threading.Thread(target=door_open_warning, args=()).start()

    threading.Thread(target=reboot_required, args=()).start()

    while True:
        time.sleep(0.01)

        for key, input in inputs.items():
            state.zone(key, input.get())

            if input.is_true:
                check(input)

        if (not triggered_lock.locked() and
                (outputs["siren1"].is_true or outputs["siren2"].is_true)):

                logging.critical("Siren(s) on outside lock!")
                wrapping_up()
                os._exit(os.EX_SOFTWARE)
