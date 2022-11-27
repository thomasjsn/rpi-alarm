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

def level(input_voltage):
    for level, voltage in battery_voltage.items():
        if input_voltage >= voltage:
            return level
