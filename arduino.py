import serial

def get_data():
    with serial.Serial('/dev/ttyUSB0', 9600, timeout=1) as ser:
        while True:
            n = "0"
            ser.write(str.encode(n))
            line = ser.readline()   # read a '\n' terminated line
            received = line.decode('utf-8').strip()
            if received == "":
                continue

            #print(received)
            received = received.split("|")

            data = {
                "voltage1": received[0],
                "voltage2": received[1],
                "temperature": received[2],
                "inputs": [bool(int(received[3]) & (1<<n)) for n in range(5)],
                "outputs": [bool(int(received[4]) & (1<<n)) for n in range(5)]
            }

            return data
