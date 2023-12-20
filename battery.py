from scipy.interpolate import interp1d


class Battery:
    capacity_voltage = {
        100: 12.89,
        90: 12.78,
        80: 12.65,
        70: 12.51,
        60: 12.41,
        50: 12.23,
        40: 12.11,
        30: 11.96,
        20: 11.81,
        10: 11.70,
        0: 11.63
    }

    interpolate_levels = interp1d(list(capacity_voltage.keys()), list(capacity_voltage.values()), 'cubic')

    def __init__(self):
        self.percentage = []
        self.battery_levels = {k: round(float(self.interpolate_levels(k)), 3) for k in range(101)}

    def level(self, input_voltage: float) -> int:
        for percentage, voltage in reversed(self.battery_levels.items()):
            if input_voltage >= voltage:
                self.percentage.append(percentage)

                if len(self.percentage) > 3:
                    self.percentage.pop(0)

                return int(round(sum(self.percentage) / len(self.percentage), 0))
