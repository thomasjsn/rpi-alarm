from scipy.interpolate import interp1d


class Battery:
    # Source: https://www.rebel-cell.com/knowledge-base/battery-capacity/
    capacity_voltage = {
        100: 12.7,
        90: 12.5,
        80: 12.42,
        70: 12.32,
        60: 12.2,
        50: 12.06,
        40: 11.9,
        30: 11.75,
        20: 11.58,
        10: 11.31,
        0: 10.5
    }

    interpolate_levels = interp1d(list(capacity_voltage.keys()), list(capacity_voltage.values()), 'cubic')

    def __init__(self):
        self.percentage = []
        self.battery_levels = {k: round(float(self.interpolate_levels(k)), 3) for k in range(101)}

    def level(self, input_voltage: float) -> int:
        for percentage, voltage in reversed(self.battery_levels.items()):
            if input_voltage >= voltage:
                self.percentage.append(percentage)

                if len(self.percentage) > 30:
                    self.percentage.pop(0)

                return int(round(sum(self.percentage) / len(self.percentage), 0))
