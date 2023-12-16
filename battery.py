class Battery:
    battery_voltage = {
        100: 12.7,
        95: 12.6,
        90: 12.5,
        85: 12.46,
        80: 12.42,
        75: 12.37,
        70: 12.32,
        65: 12.26,
        60: 12.2,
        55: 12.13,
        50: 12.06,
        45: 11.98,
        40: 11.9,
        35: 11.825,
        30: 11.75,
        25: 11.665,
        20: 11.58,
        15: 11.445,
        10: 11.31,
        5: 10.905,
        0: 10.5
    }

    def __init__(self):
        self.percentage = []

    def level(self, input_voltage: float) -> int:
        for percentage, voltage in self.battery_voltage.items():
            if input_voltage >= voltage:
                self.percentage.append(percentage)

                if len(self.percentage) > 30:
                    self.percentage.pop(0)

                return int(round(sum(self.percentage) / len(self.percentage), 0))
