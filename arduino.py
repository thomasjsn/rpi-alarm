import serial
import time
import queue

class Arduino:
    def __init__(self):
        self.data = ""
        self.commands = queue.Queue()

    def get_data(self):
        with serial.Serial('/dev/ttyUSB0', 9600, timeout=1) as ser:
            while True:
                while not self.commands.empty():
                    ser.write(str.encode(self.command.get()))

                ser.write(str.encode("0"))
                line = ser.readline()   # read a '\n' terminated line
                received = line.decode('utf-8').strip()
                if received == "":
                    continue

                #print(received)
                received = received.split("|")

                data = {
                    "voltage1": round((int(received[0]) * 4.68 / 1024 * 11.12), 2),
                    "voltage2": round((int(received[1]) * 4.68 / 1024 * 11.12), 2),
                    "temperature": float(received[2]),
                    "inputs": [not bool(int(received[3]) & (1<<n)) for n in range(5)],
                    "outputs": [bool(int(received[4]) & (1<<n)) for n in range(5)]
                }

                self.data = data
                time.sleep(1)
