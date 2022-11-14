battery_voltage = {
    100: 12.7,
    95: 12.6,
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

def level(input_voltage):
    for level, voltage in battery_voltage.items():
        if input_voltage >= voltage:
            return level
