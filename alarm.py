import time
import json
import threading
import logging
import logging.handlers
import datetime
import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import configparser
import argparse
import atexit
import os
import math
import random
from itertools import chain
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from pushover import Pushover
import hass_discovery as hass
from healthchecks import HealthChecks
from arduino import Arduino
from battery import Battery

GPIO.setmode(GPIO.BCM)   # set board mode to Broadcom
GPIO.setwarnings(False)  # don't show warnings

config = configparser.ConfigParser()
config.read('config.ini')

parser = argparse.ArgumentParser()
parser.add_argument('--silent', dest='silent', action='store_true',
                    help="suppress siren outputs")
parser.add_argument('--siren-block', dest='siren_block_relay', action='store_true',
                    help="activate siren block relay")
parser.add_argument('--payload', dest='print_payload', action='store_true',
                    help="print payload on publish")
parser.add_argument('--status', dest='print_status', action='store_true',
                    help="print status object on publish")
parser.add_argument('--serial', dest='print_serial', action='store_true',
                    help="print serial data on receive")
parser.add_argument('--timers', dest='print_timers', action='store_true',
                    help="print timers debug")
parser.add_argument('--log', dest='log_level', action='store', choices=["DEBUG", "INFO", "WARNING"],
                    help="set log level")
# parser.set_defaults(feature=True)
args = parser.parse_args()


class ArmMode(Enum):
    Home = auto()
    Away = auto()
    AwayDelayed = auto()
    Water = auto()
    Direct = auto()
    Fire = auto()
    Notify = auto()


class AlarmState(Enum):
    Disarmed = "disarmed"
    ArmedHome = "armed_home"
    ArmedAway = "armed_away"
    Triggered = "triggered"
    Pending = "pending"
    Arming = "arming"


class AlarmPanelAction(Enum):
    Disarm = auto()
    ArmHome = auto()
    ArmAway = auto()
    InvalidCode = auto()
    NotReady = auto()
    AlreadyDisarmed = auto()


class SensorValue(Enum):
    Truthy = True
    Falsy = False
    On = "on"
    # Panic = "panic"
    Emergency = "emergency"


class DevClass(Enum):
    Generic = None
    Tamper = "tamper"
    Motion = "motion"
    Door = "door"
    Moisture = "moisture"


class Zone:
    def __init__(self, key: str, label: str, dev_class: DevClass, arm_modes: list[ArmMode]):
        self.key = key
        self.label = label
        self.dev_class = dev_class
        self.arm_modes = arm_modes


class Input(Zone):
    def __init__(self, key: str, gpio: int, label: str, dev_class: DevClass, arm_modes: list[ArmMode]):
        super().__init__(key, label, dev_class, arm_modes)
        self.gpio = gpio

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
    def __init__(self, gpio: int, label: str, debug: bool = False):
        self.gpio = gpio
        self.label = label
        self.debug = debug

    def __str__(self):
        return self.label

    def set(self, value):
        if self.get() != value:
            if self in [outputs["siren1"], outputs["siren2"]] and args.silent and value:
                logging.debug("Suppressing %s, because silent", self)
                return

            GPIO.output(self.gpio, value)
            if self.debug:
                logging.debug("Output: %s set to %s", self, value)

    def get(self):
        return GPIO.input(self.gpio) == 1

    @property
    def is_true(self):
        return self.get()


class Sensor(Zone):
    def __init__(self, key: str, topic: str, field: str, value: SensorValue, label: str, dev_class: DevClass,
                 arm_modes: list[ArmMode], timeout: int = 0):
        super().__init__(key, label, dev_class, arm_modes)
        self.topic = topic
        self.field = field
        self.value = value
        self.timeout = timeout
        self.timestamp = time.time()
        self.linkquality = []

    def __str__(self):
        return self.label

    def __repr__(self):
        return f"s:{self.label}"

    def get(self):
        return state.data["zones"][self.key]

    @property
    def is_true(self):
        return self.get()


# @dataclass
# class Zones:
#     inputs: dict[str, Input]
#     sensors: dict[str, Sensor]
#
#     @property
#     def all(self) -> dict[str, Zone]:
#         return self.inputs | self.sensors
#
#     def get(self, zone_key):
#         return next((zone for key, zone in self.all if key == zone_key), None)


class ZoneTimer:
    def __init__(self, key: str, zones: list[str], label: str, blocked_state: list[str]):
        self.key = key
        self.zones = zones
        self.zone_value = True
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
    def __init__(self, topic: str, fields: dict[str, str], actions: dict[AlarmPanelAction, str], label: str,
                 set_states: dict[AlarmState, str] = None, timeout: int = 0):
        self.topic = topic
        self.fields = fields
        self.actions = actions
        self.label = label
        self.set_states = set_states or {}
        self.timeout = timeout
        self.timestamp = time.time()
        self.linkquality = []

    def __str__(self):
        return self.label

    def __repr__(self):
        return f"p:{self.label}"

    def set(self, alarm_state: AlarmState):
        if alarm_state not in self.set_states:
            return

        logging.debug("Sending state: %s to alarm panel %s", self.set_states[alarm_state], self.label)
        data = {"arm_mode": {"mode": self.set_states[alarm_state]}}
        mqtt_client.publish(f"{self.topic}/set", json.dumps(data), retain=False)

    def validate(self, transaction: str, alarm_action: AlarmPanelAction):
        if transaction is None or alarm_action not in self.actions:
            return

        logging.debug("Sending verification: %s to alarm panel %s", self.actions[alarm_action], self.label)
        data = {"arm_mode": {"transaction": int(transaction), "mode": self.actions[alarm_action]}}
        mqtt_client.publish(f"{self.topic}/set", json.dumps(data), retain=False)


inputs = {
    "ext_tamper": Input(
        key="ext_tamper",
        gpio=2,
        label="External tamper",
        dev_class=DevClass.Tamper,
        arm_modes=[ArmMode.Home, ArmMode.Away]
    ),
    "zone01": Input(
        key="zone01",
        gpio=3,
        label="1st floor hallway motion",
        dev_class=DevClass.Motion,
        arm_modes=[ArmMode.Away],
    ),
    # "zone02": Input(4),
    # "zone03": Input(17),
    # "zone04": Input(27),
    # "zone05": Input(14),
    # "zone06": Input(15),
    # "zone07": Input(18),
    # "zone08": Input(22),
    # "zone09": Input(23),
    # "1st_floor_tamper": Input(
    #    gpio=24,
    #    label="1st floor tamper",
    #    dev_class="tamper",
    #    arm_modes=[]
    #    ),
    # "zone11": None,
    # "zone12": None,
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
    "door_chime": Output(
        gpio=13,
        label="Door chime"
    ),
    # "aux1": Output(20),
    # "aux2": Output(21)
}

sensors = {
    "door1": Sensor(
        key="door1",
        topic="zigbee2mqtt/Door front",
        field="contact",
        value=SensorValue.Falsy,
        label="Front door",
        dev_class=DevClass.Door,
        arm_modes=[ArmMode.Home, ArmMode.AwayDelayed],
        timeout=3900
    ),
    "door2": Sensor(
        key="door2",
        topic="zigbee2mqtt/Door back",
        field="contact",
        value=SensorValue.Falsy,
        label="Back door",
        dev_class=DevClass.Door,
        arm_modes=[ArmMode.Home, ArmMode.Away],
        timeout=3900
    ),
    "door3": Sensor(
        key="door3",
        topic="zigbee2mqtt/Door 2nd floor",
        field="contact",
        value=SensorValue.Falsy,
        label="2nd floor door",
        dev_class=DevClass.Door,
        arm_modes=[ArmMode.Home, ArmMode.Away],
        timeout=3900
    ),
    "motion1": Sensor(
        key="motion1",
        topic="zigbee2mqtt/Motion kitchen",
        field="occupancy",
        value=SensorValue.Truthy,
        label="Kitchen motion",
        dev_class=DevClass.Motion,
        arm_modes=[ArmMode.Away],
        timeout=3900
    ),
    "motion2": Sensor(
        key="motion2",
        topic="zigbee2mqtt/Motion living room",
        field="occupancy",
        value=SensorValue.Truthy,
        label="Living room motion",
        dev_class=DevClass.Motion,
        arm_modes=[ArmMode.Away],
        timeout=3900
    ),
    "motion3": Sensor(
        key="motion3",
        topic="zigbee2mqtt/Motion entryway",
        field="occupancy",
        value=SensorValue.Truthy,
        label="Entryway motion",
        dev_class=DevClass.Motion,
        arm_modes=[ArmMode.AwayDelayed],
        timeout=3900
    ),
    "motion4": Sensor(
        key="motion4",
        topic="zigbee2mqtt/Motion 2nd floor hallway",
        field="occupancy",
        value=SensorValue.Truthy,
        label="2nd floor hallway motion",
        dev_class=DevClass.Motion,
        arm_modes=[ArmMode.Away],
        timeout=3900
    ),
    "motion5": Sensor(
        key="motion5",
        topic="zigbee2mqtt/Motion bathroom",
        field="occupancy",
        value=SensorValue.Truthy,
        label="Bathroom motion",
        dev_class=DevClass.Motion,
        arm_modes=[],
        timeout=3900
    ),
    "motion6": Sensor(
        key="motion6",
        topic="zigbee2mqtt/Motion master bedroom",
        field="occupancy",
        value=SensorValue.Truthy,
        label="Master bedroom motion",
        dev_class=DevClass.Motion,
        arm_modes=[ArmMode.Away],
        timeout=3900
    ),
    "motion7": Sensor(
        key="motion7",
        topic="zigbee2mqtt/Motion 2nd floor den",
        field="occupancy",
        value=SensorValue.Truthy,
        label="Motion 2nd floor den",
        dev_class=DevClass.Motion,
        arm_modes=[ArmMode.Away],
        timeout=3900
    ),
    "garage_motion1": Sensor(
        key="garage_motion1",
        topic="hass2mqtt/binary_sensor/garasjen_motion/state",
        field="value",
        value=SensorValue.On,
        label="Garage motion",
        dev_class=DevClass.Motion,
        arm_modes=[ArmMode.Notify]
    ),
    "garage_door1": Sensor(
        key="garage_door1",
        topic="zigbee2mqtt/Door garage side",
        field="contact",
        value=SensorValue.Falsy,
        label="Garage side door",
        dev_class=DevClass.Door,
        arm_modes=[ArmMode.Notify],
        timeout=3900
    ),
    "water_leak1": Sensor(
        key="water_leak1",
        topic="zigbee2mqtt/Water kitchen dishwasher",
        field="water_leak",
        value=SensorValue.Truthy,
        label="Kitchen dishwasher leak",
        dev_class=DevClass.Moisture,
        arm_modes=[ArmMode.Water],
        timeout=3600
    ),
    "water_leak2": Sensor(
        key="water_leak2",
        topic="zigbee2mqtt/Water kitchen sink",
        field="water_leak",
        value=SensorValue.Truthy,
        label="Kitchen sink leak",
        dev_class=DevClass.Moisture,
        arm_modes=[ArmMode.Water],
        timeout=3600
    ),
    "water_leak3": Sensor(
        key="water_leak3",
        topic="zigbee2mqtt/Water tap hatch",
        field="water_leak",
        value=SensorValue.Truthy,
        label="Outdoor tap hatch leak",
        dev_class=DevClass.Moisture,
        arm_modes=[ArmMode.Water],
        timeout=3600
    ),
    "water_leak4": Sensor(
        key="water_leak4",
        topic="zigbee2mqtt/Water home office",
        field="water_leak",
        value=SensorValue.Truthy,
        label="Home office drain leak",
        dev_class=DevClass.Moisture,
        arm_modes=[ArmMode.Water],
        timeout=3600
    ),
    "emergency1": Sensor(
        key="emergency1",
        topic="zigbee2mqtt/Panel entrance",
        field="action",
        value=SensorValue.Emergency,
        label="Emergency button entrance",
        dev_class=DevClass.Generic,
        arm_modes=[ArmMode.Direct]
    ),
    "emergency2": Sensor(
        key="emergency2",
        topic="zigbee2mqtt/Panel master bedroom",
        field="action",
        value=SensorValue.Emergency,
        label="Emergency button bedroom",
        dev_class=DevClass.Generic,
        arm_modes=[ArmMode.Direct]
    ),
    "fire_test": Sensor(
        key="fire_test",
        topic="home/alarm_test/test/fire",
        field="value",
        value=SensorValue.On,
        label="Fire test",
        dev_class=DevClass.Generic,
        arm_modes=[ArmMode.Fire]
    )
}

zones = inputs | sensors
# zones = Zones(inputs, sensors)

home_zones = [v for k, v in zones.items() if ArmMode.Home in v.arm_modes]
away_zones = [v for k, v in zones.items() if ArmMode.Away in v.arm_modes or ArmMode.AwayDelayed in v.arm_modes]
water_zones = [v for k, v in zones.items() if ArmMode.Water in v.arm_modes]
direct_zones = [v for k, v in zones.items() if ArmMode.Direct in v.arm_modes]
fire_zones = [v for k, v in zones.items() if ArmMode.Fire in v.arm_modes]
notify_zones = [v for k, v in zones.items() if ArmMode.Notify in v.arm_modes]

codes = dict(config.items("codes"))


zone_timers = {
    "hallway_motion": ZoneTimer(
        key="hallway_motion",
        zones=["zone01", "motion4"],
        # zone_value=True,
        label="Hallway motion",
        blocked_state=["armed_away"]
    ),
    "kitchen_motion": ZoneTimer(
        key="kitchen_motion",
        zones=["motion1"],
        # zone_value=True,
        label="Kitchen motion",
        blocked_state=["armed_away", "armed_home"]
    )
}

alarm_panels = {
    "home_assistant": AlarmPanel(
        topic="home/alarm_test/set",
        fields={"action": "action", "code": "code"},
        actions={
            AlarmPanelAction.Disarm: "DISARM",
            AlarmPanelAction.ArmAway: "ARM_AWAY",
            AlarmPanelAction.ArmHome: "ARM_HOME"
        },
        label="Home Assistant"
    ),
    "develco1": AlarmPanel(
        topic="zigbee2mqtt/Panel entrance",
        fields={"action": "action", "code": "action_code"},
        actions={
            AlarmPanelAction.Disarm: "disarm",
            AlarmPanelAction.ArmAway: "arm_all_zones",
            AlarmPanelAction.ArmHome: "arm_day_zones",
            AlarmPanelAction.InvalidCode: "invalid_code",
            AlarmPanelAction.NotReady: "not_ready",
            AlarmPanelAction.AlreadyDisarmed: "not_ready"
        },
        label="Entrance alarm panel",
        set_states={
            AlarmState.Disarmed: "disarm",
            AlarmState.ArmedHome: "arm_day_zones",
            AlarmState.ArmedAway: "arm_all_zones",
            AlarmState.Triggered: "in_alarm",
            AlarmState.Pending: "entry_delay",
            AlarmState.Arming: "exit_delay"
        },
        timeout=900
    ),
    "develco2": AlarmPanel(
        topic="zigbee2mqtt/Panel master bedroom",
        fields={"action": "action", "code": "action_code"},
        actions={
            AlarmPanelAction.Disarm: "disarm",
            AlarmPanelAction.ArmAway: "arm_all_zones",
            AlarmPanelAction.ArmHome: "arm_day_zones",
            AlarmPanelAction.InvalidCode: "invalid_code",
            AlarmPanelAction.NotReady: "not_ready",
            AlarmPanelAction.AlreadyDisarmed: "not_ready"
        },
        label="Master bedroom alarm panel",
        set_states={
            AlarmState.Disarmed: "disarm",
            AlarmState.ArmedHome: "arm_day_zones",
            AlarmState.ArmedAway: "arm_all_zones",
            AlarmState.Triggered: "in_alarm",
            AlarmState.Pending: "entry_delay",
            AlarmState.Arming: "exit_delay"
        },
        timeout=900
    )
}

logging_format = "%(asctime)s - %(levelname)s: %(message)s"
logging.basicConfig(format=logging_format, level=logging.DEBUG, datefmt="%H:%M:%S")

battery_log = logging.getLogger("battery")
battery_log_handler = logging.FileHandler('logs/battery.log')
battery_log_handler.setFormatter(logging.Formatter(logging_format))
battery_log.addHandler(battery_log_handler)

# rpi_gpio_log = logging.getLogger("rpi_gpio")
# rpi_gpio_log_file_handler = logging.handlers.RotatingFileHandler('logs/rpi_gpio.log',
#                                                                  maxBytes=200*1000, backupCount=5)
# rpi_gpio_log_file_handler.setFormatter(logging.Formatter(logging_format))
# rpi_gpio_log_mem_handler = logging.handlers.MemoryHandler(50, target=rpi_gpio_log_file_handler)
# rpi_gpio_log_mem_handler.setFormatter(logging.Formatter(logging_format))
# rpi_gpio_log.addHandler(rpi_gpio_log_mem_handler)

if args.log_level:
    logging.getLogger().setLevel(args.log_level)
    logging.info("Log level set to %s", args.log_level)

for gpio_input in inputs.values():
    GPIO.setup(gpio_input.gpio, GPIO.IN)

for gpio_output in outputs.values():
    GPIO.setup(gpio_output.gpio, GPIO.OUT)
    gpio_output.set(False)


def wrapping_up() -> None:
    for output in outputs.values():
        output.set(False)

    logging.info("All outputs set to False")


atexit.register(wrapping_up)


@dataclass
class StateData:
    arm_not_ready: bool = None
    auxiliary_voltage: float = None
    battery_charging: bool = None
    battery_level: int = None
    battery_low: bool = None
    battery_test_running: bool = None
    battery_voltage: float = None
    system_voltage: float = None
    config: dict[str, bool] = field(default_factory=dict)
    fault: bool = None
    reboot_required: bool = None
    state: str = None
    tamper: bool = None
    temperature: float = None
    triggered: Optional[str] = None
    water_valve: bool = None
    zigbee_bridge: bool = None
    zone_timers: dict[str, dict] = field(default_factory=dict)
    zones: dict[str, Optional[bool]] = field(default_factory=dict)

    def __getitem__(self, item):
        return getattr(self, item)

    def __setitem__(self, item, value):
        if not hasattr(self, item):
            logging.warning("Setting undefined attribute on state data object: %s", item)
        setattr(self, item, value)


class State:
    def __init__(self):
        self.data: StateData = StateData(
            state=config.get("system", "state"),
            config={
                "walk_test": config.getboolean("config", "walk_test", fallback=False),
                "door_open_warning": config.getboolean("config", "door_open_warning", fallback=True),
                "door_chime": config.getboolean("config", "door_chime", fallback=False),
                "aux_output1": config.getboolean("config", "aux_output1", fallback=False),
                "aux_output2": config.getboolean("config", "aux_output2", fallback=False)
            },
            zones={k: None for k, v in zones.items()},
            zone_timers={k: {"value": None, "attributes": {"seconds": v.seconds}} for k, v in zone_timers.items()},
        )
        self._lock: threading.Lock = threading.Lock()
        self._faults: list[str] = ["mqtt_connected"]
        self.blocked: set[Zone] = set()
        self.status: dict[str, bool] = {}
        self.code_attempts: int = 0
        self.zones_open: set[Zone] = set()
        self.notify_timestamps: dict[Zone, time] = {v: time.time() for v in notify_zones}

    def json(self) -> str:
        return json.dumps(self.data.__dict__)

    def publish(self) -> None:
        mqtt_client.publish("home/alarm_test/availability", "online", retain=True)
        mqtt_client.publish('home/alarm_test', self.json(), retain=True)

        if args.print_payload:
            print(json.dumps(self.data.__dict__, indent=2, sort_keys=True))

        if args.print_status:
            print(json.dumps(self.status, indent=2, sort_keys=True))

    @property
    def system(self) -> str:
        return self.data["state"]

    @system.setter
    def system(self, alarm_state: str) -> None:
        if alarm_state not in [e.value for e in AlarmState]:
            raise ValueError(f"State: {alarm_state} is not valid")

        with self._lock:
            logging.warning("System state changed to: %s", alarm_state)

            # if (state == "armed_away" and self.data["state"] == "triggered") or state == "disarmed":
            if alarm_state in ["disarmed", "armed_home", "armed_away"]:
                self.code_attempts = 0
                self.data["triggered"] = None

                if len(self.zones_open) > 0:
                    logging.info("Clearing open zones: %s", self.zones_open)
                    self.zones_open.clear()

            self.data["state"] = alarm_state
            self.publish()

            if alarm_state in ["disarmed", "armed_home", "armed_away"]:
                with open('config.ini', 'w') as configfile:
                    config.set("system", "state", alarm_state)
                    config.write(configfile)

            for panel in [v for k, v in alarm_panels.items() if v.set_states]:
                panel.set(AlarmState(alarm_state))

    def zone(self, zone_key: str, value: bool) -> None:
        zone = zones[zone_key]

        if self.data["zones"][zone_key] != value:
            self.data["zones"][zone_key] = value
            logging.info("Zone: %s changed to %s", zone, value)

            for timer_key, timer in zone_timers.items():
                if zone_key in timer.zones:
                    # logging.debug("Zone: %s found in timer %s", zone, timer_key)
                    self.zone_timer(timer_key)

            if value and state.data["config"]["walk_test"]:
                threading.Thread(target=buzzer_signal, args=(2, [0.2, 0.2])).start()

            if (value and state.data["config"]["door_chime"] and zone.dev_class == DevClass.Door
                    and not state.data["config"]["walk_test"] and self.system == "disarmed"
                    and not door_chime_lock.locked()):
                threading.Thread(target=door_chime, args=()).start()

            if value and self.system in ["triggered", "armed_home", "armed_away"]:
                if zone in notify_zones and (time.time() - self.notify_timestamps[zone] > 180):
                    pushover.push(f"Notify zone is open: {zone}", 1)
                    self.notify_timestamps[zone] = time.time()

            tamper_zones = {k: v.get() for k, v in zones.items() if v.dev_class == DevClass.Tamper}
            state.data["tamper"] = any(tamper_zones.values())

            for tamper_key, tamper_status in tamper_zones.items():
                state.status[f"{tamper_key}"] = not tamper_status

            clear = not any([o.get() for o in away_zones])
            self.data["arm_not_ready"] = not clear

            self.publish()

        if zone in self.blocked and value is False:
            self.blocked.remove(zone)
            logging.debug("Blocked zones: %s", self.blocked)

    def fault(self) -> None:
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

    def zone_timer(self, timer_key: str) -> None:
        timer = zone_timers[timer_key]
        timer_zones = [v for k, v in self.data["zones"].items() if k in timer.zones]
        # print(json.dumps(timer_zones, indent=2, sort_keys=True))

        # if timer.zone_value:
        #    zone_state = any(timer_zones)
        # else:
        #    zone_state = not any(timer_zones)

        zone_state = any(timer_zones)

        if zone_state:
            timer.timestamp = time.time()

        if state.system in timer.blocked_state:
            timer.cancel()

        last_msg_s = round(time.time() - timer.timestamp)
        value = last_msg_s < timer.seconds

        # if not timer.zone_value:
        #    value = not value

        if self.data["zone_timers"][timer_key]["value"] != value:
            self.data["zone_timers"][timer_key]["value"] = value
            logging.info("Zone timer: %s changed to %s", timer, value)
            self.publish()

        if args.print_timers and value:
            print(f"{timer}: {datetime.timedelta(seconds=timer.seconds-last_msg_s)}")


def buzzer(seconds: int, current_state: str) -> bool:
    logging.info("Buzzer loop started (%d seconds)", seconds)
    start_time = time.time()

    while (start_time + seconds) > time.time():
        if current_state == "arming":
            if any([o.get() for o in home_zones]):
                buzzer_signal(1, [0.2, 0.8])
            else:
                buzzer_signal(1, [0.05, 0.95])

        if current_state == "pending":
            if (start_time + (seconds/2)) > time.time():
                buzzer_signal(1, [0.05, 0.95])
            else:
                buzzer_signal(2, [0.05, 0.45])

        if state.system != current_state:
            logging.info("Buzzer loop aborted")
            return False

    logging.info("Buzzer loop completed")
    return True


def buzzer_signal(repeat: int, duration: list[float]) -> None:
    with buzzer_lock:
        if len(duration) > 2:
            time.sleep(duration[2])
        for _ in range(repeat):
            outputs["buzzer"].set(True)
            time.sleep(duration[0])
            outputs["buzzer"].set(False)
            time.sleep(duration[1])


def siren(seconds: int, zone: Zone, current_state: str) -> bool:
    logging.info("Siren loop started (%d seconds, %s, %s)",
                 seconds, zone, current_state)
    start_time = time.time()
    # zones_open = len(state.zones_open)

    while (start_time + seconds) > time.time():
        # ANSI S3.41-1990; Temporal Three or T3 pattern
        # Indoor siren uses about 0.2 seconds to react
        if zone in fire_zones:
            for _ in range(3):
                outputs["siren1"].set(True)
                time.sleep(0.7)
                outputs["siren1"].set(False)
                time.sleep(0.3)
            time.sleep(1)

        elif zone in water_zones:
            outputs["siren1"].set(True)
            time.sleep(0.5)
            outputs["siren1"].set(False)
            time.sleep(10)

        else:
            outputs["siren1"].set(True)
            # outputs["beacon"].set(True)

            if ((time.time()-start_time) > (seconds/3) and len(state.zones_open) > 1) or zone in direct_zones:
                outputs["siren2"].set(True)
            time.sleep(1)

        if state.system != current_state:
            outputs["siren1"].set(False)
            outputs["siren2"].set(False)
            # outputs["beacon"].set(False)
            logging.info("Siren loop aborted")

            return False

        # if len(state.zones_open) > zones_open:
        #    logging.warning("Open triggered zones increased, extending trigger time")
        #    logging.debug("Trigger time increased by: %d seconds", time.time() - start_time)
        #    start_time = time.time()
        #    zones_open = len(state.zones_open)

    outputs["siren1"].set(False)
    outputs["siren2"].set(False)
    # outputs["beacon"].set(False)
    logging.info("Siren loop completed")

    return True


def arming(user: str) -> None:
    state.system = "arming"
    arming_time = config.getint("times", "arming")

    if args.silent:
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


def pending(current_state: str, zone: Zone) -> None:
    delay_time = config.getint("times", "delay")

    if args.silent:
        delay_time = 10

    with pending_lock:
        state.system = "pending"
        logging.info("Pending because of zone: %s", zone)

        if buzzer(delay_time, "pending") is True:
            triggered(current_state, zone)


def triggered(current_state: str, zone: Zone) -> None:
    trigger_time = config.getint("times", "trigger")

    if args.silent:
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
        logging.warning("Triggered because of %s, zone: %s", state.data.triggered, zone)
        pushover.push(f"{state.data.triggered}, zone: {zone}", 2)

        state.blocked.add(zone)
        logging.debug("Blocked zones: %s", state.blocked)

        if siren(trigger_time, zone, "triggered") is True:
            state.system = current_state


def disarmed(user: str) -> None:
    state.system = "disarmed"
    pushover.push(f"System disarmed, by {user}")
    buzzer_signal(2, [0.05, 0.15])


def armed_home(user: str) -> None:
    active_home_zones = [o.label for o in home_zones if o.get()]

    if not active_home_zones:
        state.system = "armed_home"
        pushover.push(f"System armed home, by {user}")
        buzzer_signal(1, [0.05, 0.05])
    else:
        logging.error("Arm home failed, not clear: %s", active_home_zones)
        state.system = "disarmed"
        active_home_zones_str = ", ".join(active_home_zones)
        pushover.push(f"Arm home failed, not clear: {active_home_zones_str}", 1, {"sound": "siren"})
        buzzer_signal(1, [1, 0])


def water_alarm() -> None:
    with water_alarm_lock:
        water_alarm_time = time.time()
        logging.warning("Entered water alarm lock!")

        arduino.commands.put([3, True])  # Water valve relay
        arduino.commands.put([4, True])  # Dishwasher relay (NC)

        # Keep in loop until manually reset
        while not arduino.data.inputs[4]:
            if math.floor(time.time() - water_alarm_time) % 30 == 0:
                buzzer_signal(1, [0.5, 0.5])
                buzzer_signal(2, [0.1, 0.2])
            else:
                time.sleep(1)

        logging.info("Leaving water alarm lock.")

        # Turn water back on if manual switch enabled
        if arduino.data.inputs[3]:
            arduino.commands.put([3, False])  # Water valve relay

        arduino.commands.put([4, False])  # Dishwasher relay (NC)


def run_led() -> None:
    while True:
        run_led_output = "led_red" if state.data["fault"] else "led_green"

        if state.system == "disarmed":
            time.sleep(1.5)
        else:
            time.sleep(0.5)

        outputs[run_led_output].set(True)
        time.sleep(0.5)
        outputs[run_led_output].set(False)


def check_zone(zone: Zone) -> None:
    if zone in fire_zones or (state.system != "armed_away" and zone in direct_zones):
        if not triggered_lock.locked():
            threading.Thread(target=triggered, args=(state.system, zone,)).start()

    if zone in water_zones:
        if not triggered_lock.locked():
            threading.Thread(target=water_alarm, args=()).start()
            threading.Thread(target=triggered, args=(state.system, zone,)).start()

    if zone in state.blocked:
        return

    if state.system in ["armed_away", "pending"] and zone in away_zones:
        if ArmMode.AwayDelayed in zone.arm_modes and not pending_lock.locked():
            threading.Thread(target=pending, args=("armed_away", zone,)).start()
        if ArmMode.Away in zone.arm_modes and not triggered_lock.locked():
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
def on_connect(client: mqtt.Client, userdata, flags: dict[str, int], rc: int) -> None:
    logging.info("Connected to MQTT broker with result code %s", rc)

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.

    topics = set()
    topics.add("zigbee2mqtt/bridge/state")

    for option in ["config", "action"]:
        topics.add(f"home/alarm_test/{option}")

    for panel in alarm_panels.values():
        topics.add(panel.topic)

    for sensor in sensors.values():
        topics.add(sensor.topic)

    topic_tuples = [(topic, 0) for topic in topics]
    logging.debug("Topics: %s", topic_tuples)

    client.subscribe(topic_tuples)

    if rc == 0:
        client.connected_flag = True
        state.status["mqtt_connected"] = True
        hass.discovery(client, zones, zone_timers)
    else:
        client.bad_connection_flag = True
        print("Bad connection, returned code: ", str(rc))


def on_disconnect(client: mqtt.Client, userdata, rc: int) -> None:
    logging.warning("Disconnecting reason %s", rc)
    client.connected_flag = False
    state.status["mqtt_connected"] = False
    client.disconnect_flag = True


# The callback for when a PUBLISH message is received from the server.
def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    logging.debug("Received message: %s %s", msg.topic, msg.payload.decode('utf-8'))

    if msg.payload.decode('utf-8') == "":
        logging.warning("Received empty payload, discarded")
        return

    try:
        y = json.loads(str(msg.payload.decode('utf-8')))
    except json.JSONDecodeError:
        y = {"value": msg.payload.decode('utf-8')}
        logging.debug("Unable to decode JSON, created object %s", y)

    if msg.topic == "zigbee2mqtt/bridge/state" and "state" in y:
        state.status["zigbee_bridge"] = y["state"] == "online"
        state.data["zigbee_bridge"] = state.status["zigbee_bridge"]
        return

    if msg.topic == "home/alarm_test/config" and all(k in y for k in ("option", "value")):
        cfg_option = y["option"]
        cfg_value = y["value"]

        with open('config.ini', 'w') as configfile:
            config.set('config', cfg_option, str(cfg_value))
            config.write(configfile)

        logging.info("Config option: %s changed to %s", cfg_option, cfg_value)
        state.data["config"][cfg_option] = cfg_value
        state.publish()
        return

    if msg.topic == "home/alarm_test/action" and all(k in y for k in ("option", "value")):
        act_option = y["option"]
        act_value = y["value"]

        logging.info("Action triggered: %s, with value: %s", act_option, act_value)

        if act_option == "siren_test" and act_value:
            # arduino.commands.put([1, True]) # Siren block relay
            with pending_lock:
                buzzer_signal(7, [0.1, 0.9])
                buzzer_signal(1, [2.5, 0.5])
            with triggered_lock:
                siren_test_zones = [v for k, v in zones.items() if v.dev_class == DevClass.Tamper]
                if siren_test_zones and len(zones) > 2:
                    state.zones_open.update(list(zones.values())[:2])
                    siren(3, siren_test_zones[0], "disarmed")  # use first tamper zone to test
                    # state.zones_open.clear()
                else:
                    logging.error("Not enough zones defined, unable to run siren test!")
            # arduino.commands.put([1, False]) # Siren block relay

        if act_option == "zone_timer_cancel" and act_value in zone_timers:
            timer = zone_timers[act_value]
            timer.cancel()

        if act_option == "battery_test" and act_value:
            if not battery_test_lock.locked():
                threading.Thread(target=battery_test, args=(), daemon=True).start()
            else:
                logging.error("Battery test already running!")

        if act_option == "water_valve_test" and act_value:
            if not water_valve_test_lock.locked():
                threading.Thread(target=water_valve_test, args=()).start()
            else:
                logging.error("Water valve test already running!")

        if act_option == "water_alarm_test" and act_value:
            with pending_lock:
                buzzer_signal(7, [0.1, 0.9])
                buzzer_signal(1, [2.5, 0.5])
            if water_zones:
                check_zone(random.choice(water_zones))  # use random water sensor to test
            else:
                logging.error("No water zones defined, unable to run water alarm test!")

        if act_option == "fire_alarm_test" and act_value:
            with pending_lock:
                buzzer_signal(7, [0.1, 0.9])
                buzzer_signal(1, [2.5, 0.5])
            if water_zones:
                check_zone(random.choice(fire_zones))  # use random fire sensor to test
            else:
                logging.error("No fire zones defined, unable to run fire alarm test!")

        if act_option == "water_valve_set":
            arduino.commands.put([3, not act_value])
            # logging.info("Water valve action: %s", act_value)

        return

    for key, panel in alarm_panels.items():
        if msg.topic == panel.topic:
            panel.timestamp = time.time()

            if "battery" in y:
                if isinstance(y["battery"], int):
                    # logging.debug("Found battery level %s on panel %s", y["battery"], panel)
                    state.status[f"panel_{key}_battery"] = int(y["battery"]) > 20

            if "linkquality" in y:
                # logging.debug("Found link quality %s on panel %s", y["linkquality"], panel)
                panel.linkquality.append(int(y["linkquality"]))

                if len(panel.linkquality) > 3:
                    panel.linkquality.pop(0)

                state.status[f"panel_{key}_linkquality"] = max(panel.linkquality) > 20
                # print(panel.linkquality)

        if msg.topic == panel.topic and panel.fields["action"] in y:
            action = y[panel.fields["action"]]
            code = str(y.get(panel.fields["code"])).lower()
            action_transaction = y.get("action_transaction")

            if msg.retain == 1:
                logging.warning("Discarding action: %s, in retained message from alarm panel: %s", action, panel)
                continue

            # if panel.emergency and action == panel.emergency:
            #    logging.warning(f"Emergency from panel: {panel.label}")

            if code in codes:
                user = codes[code]
                logging.info("Panel action, %s: %s by %s (%s)", panel, action, user, action_transaction)

                if action == panel.actions[AlarmPanelAction.Disarm]:
                    if state.system == "disarmed":
                        panel.validate(action_transaction, AlarmPanelAction.AlreadyDisarmed)
                    else:
                        panel.validate(action_transaction, AlarmPanelAction.Disarm)
                        threading.Thread(target=disarmed, args=(user,)).start()

                elif action == panel.actions[AlarmPanelAction.ArmAway]:
                    panel.validate(action_transaction, AlarmPanelAction.ArmAway)
                    threading.Thread(target=arming, args=(user,)).start()

                elif action == panel.actions[AlarmPanelAction.ArmHome]:
                    if any([o.get() for o in home_zones]):
                        panel.validate(action_transaction, AlarmPanelAction.NotReady)
                    else:
                        panel.validate(action_transaction, AlarmPanelAction.ArmHome)
                        threading.Thread(target=armed_home, args=(user,)).start()

                else:
                    logging.warning("Unknown action: %s, from alarm panel: %s", action, panel)

            elif code is not None:
                state.code_attempts += 1
                logging.warning("Invalid code: %s, attempt: %d", code, state.code_attempts)
                # buzzer_signal(1, [1, 0])
                panel.validate(action_transaction, AlarmPanelAction.InvalidCode)
                pushover.push(f"Invalid code entered on alarm panel: {panel}")

    for key, sensor in sensors.items():
        if msg.topic == sensor.topic and sensor.field in y:
            sensor.timestamp = time.time()

            state.zone(key, y[sensor.field] == sensor.value.value)

            if y[sensor.field] == sensor.value.value:
                if msg.retain == 1 and sensor in chain(direct_zones, fire_zones):
                    logging.warning("Discarding active sensor: %s, in retained message", sensor)
                    continue

                check_zone(sensor)

            if "battery" in y:
                if isinstance(y["battery"], int):
                    # logging.debug("Found battery level %s on sensor %s", y["battery"], sensor)
                    state.status[f"sensor_{key}_battery"] = int(y["battery"]) > 20

            if "linkquality" in y:
                # logging.debug("Found link quality %s on sensor %s", y["linkquality"], sensor)
                sensor.linkquality.append(int(y["linkquality"]))

                if len(sensor.linkquality) > 3:
                    sensor.linkquality.pop(0)

                state.status[f"sensor_{key}_linkquality"] = max(sensor.linkquality) > 20
                # print(sensor.linkquality)


def status_check() -> None:
    while True:
        for key, device in (sensors.items() | alarm_panels.items()):
            if device.timeout == 0:
                continue

            last_msg_s = round(time.time() - device.timestamp)
            state.status[f"device_{key}_timeout"] = last_msg_s < device.timeout
            state.status[f"device_{key}_lost"] = last_msg_s < 86400

        state.status["code_attempts"] = state.code_attempts < 3
        state.status["arduino_data"] = round(time.time() - arduino.timestamp) < 10

        for key, timer in zone_timers.items():
            state.zone_timer(key)

        state.fault()
        time.sleep(1)


def heartbeat_ping() -> None:
    hc_uuid = config.get("healthchecks.uuid", "heartbeat", fallback=None)
    hc_heartbeat = HealthChecks(hc_uuid)

    if not hc_uuid:
        logging.debug("Healthchecks UUID not found, aborting ping.")
        return

    logging.info("Starting Healthchecks ping with UUID %s", hc_uuid)

    while True:
        hc_status = hc_heartbeat.ping()
        state.status["healthchecks"] = hc_status

        time.sleep(60)


def serial_data() -> None:
    water_valve_switch = True

    while True:
        arduino.data_ready.wait()
        data = arduino.data

        if args.print_serial:
            print(arduino.timestamp)
            print(json.dumps(data.__dict__, indent=2, sort_keys=True))

        try:
            state.data["temperature"] = data.temperature
            state.data["auxiliary_voltage"] = data.aux12_voltage
            state.data["system_voltage"] = data.system_voltage

            state.data["battery_voltage"] = data.battery_voltage
            state.data["battery_level"] = battery.level(data.battery_voltage)
            state.data["battery_low"] = data.battery_voltage < 12
            state.data["battery_charging"] = data.battery_voltage > 13 and not data.outputs[1]

            state.status["auxiliary_voltage"] = 12 < data.aux12_voltage < 12.5
            state.status["battery_voltage"] = 12 < data.battery_voltage < 15
            state.status["system_voltage"] = 5 < data.system_voltage < 5.2
            state.status["cabinet_temp"] = data.temperature < 30

            state.data["water_valve"] = not data.outputs[2]

        except ValueError:
            logging.error("ValueError on data from Arduino device")

        # state.status["siren1_output"] = outputs["siren1"].get() == data["inputs"][1]
        # state.status["siren2_output"] = outputs["siren2"].get() == data["inputs"][2]
        state.status["siren_block"] = data.outputs[0] is False

        state.data["battery_test_running"] = battery_test_lock.locked()

        if data.outputs[4] != state.data["config"]["aux_output1"]:
            arduino.commands.put([5, state.data["config"]["aux_output1"]])
        if data.outputs[5] != state.data["config"]["aux_output2"]:
            arduino.commands.put([6, state.data["config"]["aux_output2"]])

        if data.inputs[3] != water_valve_switch and not water_alarm_lock.locked():
            arduino.commands.put([3, not data.inputs[3]])
            water_valve_switch = data.inputs[3]
            logging.info("Water valve switch changed state: %s", data.inputs[3])

        arduino.data_ready.clear()

        if round(time.time(), 0) % 10 == 0:
            state.publish()


def door_open_warning() -> None:
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


def battery_test() -> None:
    with battery_test_lock:
        hc_battery_test = HealthChecks(config.get("healthchecks.uuid", "battery_test", fallback=None))

        arduino.commands.put([2, True])  # Disable charger
        arduino.commands.join()

        hc_battery_test.start()
        start_time = time.time()
        battery_log.info("Battery test started at %s V", arduino.data.battery_voltage)

        while state.data["battery_level"] >= 50:
            time.sleep(1)

        hc_battery_test.stop()
        test_time = round(time.time() - start_time, 0)
        battery_log.info("Battery test completed at %s V and %s %%, took: %s",
                         arduino.data.battery_voltage, state.data["battery_level"],
                         datetime.timedelta(seconds=test_time))
        pushover.push(f"Battery test completed, took {datetime.timedelta(seconds=test_time)}")
        arduino.commands.put([2, False])  # Re-enable charger
        arduino.commands.join()


def water_valve_test() -> None:
    with water_valve_test_lock:
        hc_water_valve = HealthChecks(config.get("healthchecks.uuid", "water_valve_test", fallback=None))

        if arduino.data.outputs[2] or water_alarm_lock.locked():
            logging.error("Can not run water valve test if valve is already active or water alarm is triggered")
            return

        hc_water_valve.start()
        logging.info("Water valve test started")

        for valve_state in [True, False]:
            arduino.commands.put([3, valve_state])  # Water valve relay
            arduino.commands.join()
            time.sleep(1)

        hc_water_valve.stop()
        logging.info("Water valve test completed")


def door_chime() -> None:
    with door_chime_lock:
        outputs["door_chime"].set(True)
        time.sleep(1)
        outputs["door_chime"].set(False)
        time.sleep(30)


def check_reboot_required() -> None:
    while True:
        reboot_is_required = os.path.isfile("/var/run/reboot-required")
        state.data["reboot_required"] = reboot_is_required

        if reboot_is_required:
            logging.warning("Reboot required!")

        time.sleep(60*60)


mqtt_client = mqtt.Client(config.get("mqtt", "client_id"))
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message
mqtt_client.will_set("home/alarm_test/availability", "offline")

for attempt in range(5):
    try:
        mqtt_client.connect(config.get("mqtt", "host"))
        mqtt_client.loop_start()
    except OSError:
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

arduino = Arduino()
battery = Battery()

# Since the Arduino resets when DTR is pulled low, the
# siren block is removed when starting up.
if args.siren_block_relay:
    arduino.commands.put([1, True])  # Siren block relay
    logging.warning("Sirens blocked, siren block active!")

if args.silent:
    logging.warning("Sirens suppressed, silent mode active!")

# for zone_key, zone in zones.items():
#     zone.key = zone_key
#     state.data["zones"][zone_key] = None
#
# for timer_key, timer in zone_timers.items():
#     timer.key = timer_key
#     state.data["zone_timers"][timer_key] = {
#         "value": None,
#         "attributes": {
#             "seconds": timer.seconds
#         }
#     }

pending_lock = threading.Lock()
triggered_lock = threading.Lock()
buzzer_lock = threading.Lock()
water_alarm_lock = threading.Lock()

battery_test_lock = threading.Lock()
water_valve_test_lock = threading.Lock()
door_chime_lock = threading.Lock()

logging.info("Arm home zones: %s", home_zones)
logging.info("Arm away zones: %s", away_zones)
logging.info("Water alarm zones: %s", water_zones)
logging.info("Direct alarm zones: %s", direct_zones)
logging.info("Fire alarm zones: %s", fire_zones)
logging.info("Notify zones: %s", notify_zones)

# for notify in notify_zones:
#     state.notify_timestamps[notify] = time.time()

passive_zones = [v for k, v in zones.items() if not v.arm_modes]
logging.info("Passive zones: %s", passive_zones)

if __name__ == "__main__":
    threading.Thread(target=run_led, args=(), daemon=True).start()

    threading.Thread(target=status_check, args=(), daemon=True).start()

    threading.Thread(target=heartbeat_ping, args=(), daemon=True).start()

    threading.Thread(target=arduino.get_data, args=(), daemon=True).start()
    threading.Thread(target=serial_data, args=(), daemon=True).start()

    threading.Thread(target=door_open_warning, args=(), daemon=True).start()

    threading.Thread(target=check_reboot_required, args=(), daemon=True).start()

    input_active_counter: dict[str, int] = {}

    while True:
        time.sleep(0.01)  # Wait 10 ms

        # This loop takes less than 100 micro seconds to complete
        for input_key, gpio_input in inputs.items():
            state.zone(input_key, gpio_input.get())

            if input_key not in input_active_counter:
                input_active_counter[input_key] = 0

            if gpio_input.is_true:
                input_active_counter[input_key] += 1

                # Debounce zone inputs, must be active for 5 cycles = 50 ms
                if input_active_counter[input_key] > 5:
                    check_zone(gpio_input)
            else:
                # if input_active_counter[input_key] > 0:
                #     rpi_gpio_log.debug("Zone: %s was active for %s cycles", gpio_input, input_active_counter[input_key])
                input_active_counter[input_key] = 0

        if not triggered_lock.locked() and (outputs["siren1"].is_true or outputs["siren2"].is_true):
            logging.critical("Siren(s) on outside lock!")
            wrapping_up()

            raise SystemError("Siren(s) on outside lock!")
