import time
import json
import threading
import logging
import datetime
import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import http.client, urllib
import configparser

GPIO.setmode(GPIO.BCM)   # set board mode to Broadcom
GPIO.setwarnings(False)  # don't show warnings

config = configparser.ConfigParser()
config.read('config.ini')


class Input:
    def __init__(self, gpio, label=None, dev_class=None, delay=False):
        self.gpio = gpio
        self.label = label
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
        self.label = label
        self.debug = debug

    def __str__(self):
        return self.label

    def set(self, state):
        if self.get() != state:
            GPIO.output(self.gpio, state)
            if self.debug:
                logging.debug("Output: %s set to %s", self, state)

    def get(self):
        return GPIO.input(self.gpio) == 1

    @property
    def is_true(self):
        return self.get()


class Sensor:
    def __init__(self, topic, field, value, label=None, delay=False, timeout=0):
        self.topic = topic
        self.field = field
        self.value = value
        self.label = label
        self.delay = delay
        self.timeout = timeout
        self.timestamp = time.time()

    def __str__(self):
        return self.label

    def __repr__(self):
        return f"s:{self.label}"


class Entity:
    def __init__(self, field, component, label=None, dev_class=None):
        self.field = field
        self.component = component
        self.label = label
        self.dev_class = dev_class

    def __str__(self):
        return self.label


inputs = {
    "tamper": Input(
        gpio=2,
        label="Tamper",
        dev_class="tamper"
        ),
    #"zone1": Input(3, "Hallway 1st floor", "motion"),
    #"zone2": Input(4, "Hallway 2st floor", "motion"),
    #"zone3": Input(17),
    #"zone4": Input(27)
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
        gpio=13,
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
        )
}

sensors = {
    "door1": Sensor(
        topic="zigbee2mqtt/Door front",
        field="contact",
        value=False,
        label="Front door",
        delay=True,
        timeout=3600
        ),
    "motion1": Sensor(
        topic="zigbee2mqtt/Motion kitchen",
        field="occupancy",
        value=True,
        label="Kitchen",
        timeout=3600
        ),
    "motion2": Sensor(
        topic="zigbee2mqtt/Motion 2nd floor",
        field="occupancy",
        value=True,
        label="2nd floor",
        timeout=3600
        ),
    "panel_tamper": Sensor(
        topic="zigbee2mqtt/Alarm panel",
        field="tamper",
        value=True,
        label="Panel tamper",
        timeout=2100
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

zones = inputs | sensors

#codes = {
#    "1234": "Test"
#}
codes = dict(config["codes"])

entities = {
    "triggered_zone": Entity(
        field="triggered.zone",
        component="sensor",
        label="Triggered zone"
        )
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
            "fault": False,
            "zones": {},
            "triggered": {
                "zone": None,
                "timestamp": None
            }
        }
        self._lock = threading.Lock()
        self.connected = False
        self.blocked = set()
        self.status = {}

    def json(self):
        return json.dumps(self.data)

    def publish(self):
        client.publish("home/alarm_test/availability", "online", retain=True)
        client.publish('home/alarm_test', self.json(), retain=True)

    @property
    def system(self):
        return self.data["state"]

    @system.setter
    def system(self, state):
        with self._lock:
            logging.warning("System state changed to: %s", state)
            self.data["state"] = state
            self.publish()

    def triggered(self, zone):
        with self._lock:
            self.data["triggered"]["zone"] = str(zone)
            self.data["triggered"]["timestamp"] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.system = "triggered"

    def zone(self, zone_key, value):
        zone = zones[zone_key]
        clear = not any(self.data["zones"].values())
        #clear = True

        if self.data["clear"] is not clear:
            self.data["clear"] = clear
            logging.info("All zones are clear: %s", self.data['clear'])

        if self.data["zones"][zone_key] != value:
            self.data["zones"][zone_key] = value
            logging.info("Zone: %s changed to %s", zone, value)
            self.publish()
            #print(json.dumps(self.data, indent=4, sort_keys=True))

            tamper_zones = {k: v for k, v in self.data["zones"].items() if k.endswith('tamper')}
            state.status["tamper"] = not any(tamper_zones.values())

        if zone in self.blocked and value is False:
            self.blocked.remove(zone)
            logging.debug("Blocked zones: %s", self.blocked)

    def fault(self):
        fault = not all(self.status.values())

        if self.data["fault"] is not fault:
            self.data["fault"] = fault

            if fault:
                faulted_status = ",".join([k for k, v in self.status.items() if not v])
                logging.error("System fault: %s", faulted_status)
                pushover.push(f"System fault: {faulted_status}")


class Pushover:
    def __init__(self):
        self.token = config["pushover"]["token"]
        self.user = config["pushover"]["user"]

    def _push(self, message, priority=0, data={}):
        if priority == 2:
            data = {
                "sound": "alien",
                "priority": 2,
                "retry": 30,
                "expire": 3600
            }

        conn = http.client.HTTPSConnection("api.pushover.net:443")
        conn.request("POST", "/1/messages.json",
                     urllib.parse.urlencode({
                         "token": self.token,
                         "user": self.user,
                         "message": message,
                         "timestamp": time.time(),
                         "sound": "gamelan"
                     } | data), {"Content-type": "application/x-www-form-urlencoded"})
        conn.getresponse()

    def push(self, message, priority=0, data={}):
        threading.Thread(target=self._push, args=(message, priority, data,)).start()


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
        #outputs["buzzer"].set(True)
        outputs["siren1"].set(True)

        if x > (i/3) and i >= 30:
            outputs["siren2"].set(True)

        if zone == zones["tamper"]:
            time.sleep(0.5)
            #outputs["buzzer"].set(False)
            outputs["siren1"].set(False)
            time.sleep(0.5)

        elif zone == zones["emergency"]:
            time.sleep(0.1)
            break

        else:
            time.sleep(1)

        if state.system != current_state:
            #outputs["buzzer"].set(False)
            outputs["siren1"].set(False)
            outputs["siren2"].set(False)
            logging.info("Siren loop aborted")
            return False

    #outputs["buzzer"].set(False)
    outputs["siren1"].set(False)
    outputs["siren2"].set(False)

    logging.info("Siren loop completed")
    return True


def arming(user):
    state.system = "arming"
    if buzzer(30, [0.1, 0.9], "arming") is True:
        if state.data["clear"]:
            state.system = "armed_away"
            pushover.push(f"System armed away, by {user}")
        else:
            logging.error("Unable to arm, zones not clear")
            state.system = "disarmed"
            pushover.push("Arming failed, not clear", 1, {"sound": "siren"})


def pending(current_state, zone):
    with pending_lock:
        state.system = "pending"
        logging.info("Pending because of zone: %s", zone)

        if buzzer(30, [0.5, 0.5], "pending") is True:
            triggered(current_state, zone)


def triggered(current_state, zone):
    with triggered_lock:
        state.triggered(zone)
        logging.info("Triggered because of zone: %s", zone)
        pushover.push(f"Triggered, zone: {zone}", 2)

        state.blocked.add(zone)
        logging.debug("Blocked zones: %s", state.blocked)

        if siren(30, zone, "triggered") is True:
            state.system = current_state


def disarmed(user):
    state.system = "disarmed"
    pushover.push(f"System disarmed, by {user}")
    buzzer(2, [0.1, 0.1], "disarmed")


def armed_home(user):
    state.system = "armed_home"
    pushover.push(f"System armed home, by {user}")
    buzzer(1, [0.2, 0.2], "armed_home")


def run_led():
    while True:
        run_led = "led_red" if state.data["fault"] else "led_green"

        if state.system == "disarmed":
            time.sleep(2.5)
        else:
            time.sleep(0.5)

        outputs[run_led].set(True)
        time.sleep(0.5)
        outputs[run_led].set(False)


def check(zone, delayed=False):
    if zone in [zones["panic"], zones["emergency"]]:
        if not triggered_lock.locked():
            threading.Thread(target=triggered, args=(state.system, zone,)).start()

    if zone in state.blocked:
        return

    if state.system in ["armed_away", "pending"]:
        if delayed and not pending_lock.locked():
            threading.Thread(target=pending, args=("armed_away", zone,)).start()
        elif not triggered_lock.locked():
            threading.Thread(target=triggered, args=("armed_away", zone,)).start()

    if state.system == "armed_home":
        if zone in [zones["door1"], zones["tamper"]] and not triggered_lock.locked():
            threading.Thread(target=triggered, args=("armed_home", zone,)).start()


def hass_discovery():
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
            "device_class": entity.dev_class,
            "value_template": "{{ value_json." + entity.field + " }}"
        }

        #print(json.dumps(payload, indent=4, sort_keys=True))
        client.publish(f'homeassistant/{entity.component}/rpi_alarm/{key}/config', json.dumps(payload))

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
        client.publish(f'homeassistant/binary_sensor/rpi_alarm/{key}/config', json.dumps(payload))


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    logging.info("Connected with result code %s", rc)

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("home/alarm_test/set")
    client.subscribe("zigbee2mqtt/bridge/state")

    topics = set([sensor.topic for sensor in sensors.values()])

    for topic in topics:
        client.subscribe(topic)

    #client.subscribe("zigbee2mqtt/Alarm panel")
    #client.subscribe("zigbee2mqtt/Door front")
    #client.subscribe("zigbee2mqtt/Motion 2nd floor")
    #client.subscribe("zigbee2mqtt/Motion kitchen")

    if rc == 0:
        client.connected_flag = True
        hass_discovery()
        client.publish("home/alarm_test/availability", "online")
        state.status["connected"] = True
    else:
        client.bad_connection_flag = True
        print("Bad connection, returned code: ", str(rc))


def on_disconnect(client, userdata, rc):
    logging.info("Disconnecting reason %s", rc)
    client.connected_flag = False
    client.disconnect_flag = True
    state.status["connected"] = True


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    logging.debug("Received message: %s %s", msg.topic, msg.payload.decode('utf-8'))

#    if msg.topic.endswith("availability"):
#        return

    if msg.topic == "zigbee2mqtt/bridge/state":
        state.status["bridge"] = msg.payload.decode('utf-8') == "online"
        return

    y = json.loads(str(msg.payload.decode('utf-8')))

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
            logging.error("Bad code: %s", code)

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
            logging.error("Bad code: %s", code)

#        state.zone("panel_tamper", y["tamper"] is True)
#        if y["tamper"] is True:
#            check("Panel tamper", False)

    for key, sensor in sensors.items():
        if msg.topic == sensor.topic:
            state.zone(key, y[sensor.field] == sensor.value)

            if y[sensor.field] == sensor.value:
                check(sensor, sensor.delay)

            sensor.timestamp = time.time()

#    if msg.topic == "zigbee2mqtt/Door front":
#        state.zone("door1", y["contact"] is False)
#        if y["contact"] is False:
#            check(sensors["door1"], True)
#
#    if msg.topic == "zigbee2mqtt/Motion kitchen":
#        state.zone("motion1", y["occupancy"] is True)
#        if y["occupancy"] is True:
#            check(sensors["motion1"], False)
#
#    if msg.topic == "zigbee2mqtt/Motion 2nd floor":
#        state.zone("motion2", y["occupancy"] is True)
#        if y["occupancy"] is True:
#            check(sensors["motion2"], False)


def status_check():
    while True:
        for key, sensor in sensors.items():
            if sensor.timeout == 0:
                continue

            last_msg_s = round(time.time() - sensor.timestamp)

            if last_msg_s > sensor.timeout:
                state.status["sensors"] = False
                break

        else:
            state.status["sensors"] = True

        state.fault()
        time.sleep(1)


client = mqtt.Client('alarm-test')
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message
client.will_set("home/alarm_test/availability", "offline")
client.connect("mqtt.lan.uctrl.net")
client.loop_start()


state = State()
pushover = Pushover()

for z in zones:
    state.data["zones"][z] = False

state.publish()

pending_lock = threading.Lock()
triggered_lock = threading.Lock()

if __name__ == "__main__":
    run_led = threading.Thread(target=run_led, args=())
    run_led.start()

    status_check = threading.Thread(target=status_check, args=())
    status_check.start()

    while True:
        time.sleep(0.01)

        for key, input in inputs.items():
            state.zone(key, input.get())

            if input.is_true:
                check(input, input.delay)
