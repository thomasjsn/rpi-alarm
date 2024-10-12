from dataclasses import dataclass
from typing import Optional


@dataclass
class Entity:
    id: str
    data_key: Optional[str]
    component: str
    label: str
    dev_class: str = None
    state_class: str = None
    unit: str = None
    category: str = None
    icon: str = None

    def __str__(self):
        return self.label


entities = [
    Entity(
        id="triggered",
        data_key="triggered",
        component="sensor",
        dev_class="enum",
        label="Triggered alarm",
        icon="alarm-bell",
        category="diagnostic"
    ),
    Entity(
        id="safe_to_arm",
        data_key="arm_not_ready",
        component="binary_sensor",
        dev_class="safety",
        label="Ready to arm",
        category="diagnostic"
    ),
    Entity(
        id="system_fault",
        data_key="fault",
        component="binary_sensor",
        dev_class="problem",
        label="System status",
        category="diagnostic"
    ),
    Entity(
        id="system_tamper",
        data_key="tamper",
        component="binary_sensor",
        dev_class="tamper",
        label="System tamper",
        category="diagnostic"
    ),
    Entity(
        id="system_temperature",
        data_key="temperature",
        component="sensor",
        dev_class="temperature",
        state_class="measurement",
        unit="°C",
        label="System temperature",
        category="diagnostic"
    ),
    Entity(
        id="battery_voltage",
        data_key="battery_voltage",
        component="sensor",
        dev_class="voltage",
        state_class="measurement",
        unit="V",
        label="Battery voltage",
        category="diagnostic"
    ),
    Entity(
        id="battery_level",
        data_key="battery_level",
        component="sensor",
        dev_class="battery",
        state_class="measurement",
        unit="%",
        label="Battery",
        category="diagnostic"
    ),
    Entity(
        id="battery_low",
        data_key="battery_low",
        component="binary_sensor",
        dev_class="battery",
        label="Battery low",
        category="diagnostic"
    ),
    Entity(
        id="battery_charging",
        data_key="battery_charging",
        component="binary_sensor",
        dev_class="battery_charging",
        label="Battery charging",
        category="diagnostic"
    ),
    Entity(
        id="battery_test_running",
        data_key="battery_test_running",
        component="binary_sensor",
        dev_class="running",
        label="Battery test",
        category="diagnostic"
    ),
    Entity(
        id="auxiliary_voltage",
        data_key="auxiliary_voltage",
        component="sensor",
        dev_class="voltage",
        state_class="measurement",
        unit="V",
        label="Auxiliary voltage",
        category="diagnostic"
    ),
    Entity(
        id="walk_test",
        data_key="config.walk_test",
        component="switch",
        label="Walk test",
        icon="walk",
        category="config"
    ),
    Entity(
        id="door_open_warning",
        data_key="config.door_open_warning",
        component="switch",
        label="Door open warning",
        icon="door-open",
        category="config"
    ),
    Entity(
        id="door_chime",
        data_key="config.door_chime",
        component="switch",
        label="Door chime",
        icon="door-open",
        category="config"
    ),
    Entity(
        id="siren_test",
        data_key=None,
        component="button",
        label="Siren test",
        icon="bullhorn",
        category="diagnostic"
    ),
    Entity(
        id="battery_test",
        data_key=None,
        component="button",
        label="Battery test",
        icon="battery-clock",
        category="diagnostic"
    ),
    Entity(
        id="water_alarm_test",
        data_key=None,
        component="button",
        label="Water alarm test",
        icon="water-alert",
        category="diagnostic"
    ),
    Entity(
        id="fire_alarm_test",
        data_key=None,
        component="button",
        label="Fire alarm test",
        icon="fire-alert",
        category="diagnostic"
    ),
    Entity(
        id="zigbee_bridge",
        data_key="zigbee_bridge",
        component="binary_sensor",
        dev_class="connectivity",
        label="Zigbee bridge",
        category="diagnostic"
    ),
    Entity(
        id="reboot_required",
        data_key="reboot_required",
        component="binary_sensor",
        dev_class="update",
        label="Reboot required",
        category="diagnostic"
    ),
    Entity(
        id="water_valve",
        data_key="water_valve",
        component="valve",
        dev_class="water",
        label="Water valve"
    ),
    Entity(
        id="aux_output1",
        data_key="config.aux_output1",
        component="switch",
        label="Auxiliary output 1"
    ),
    Entity(
        id="aux_output2",
        data_key="config.aux_output2",
        component="switch",
        label="Auxiliary output 2"
    )
]
