import serial
import time
import queue

class Arduino:
    def __init__(self, logging):
        self.data = {}
        self.commands = queue.Queue()
        self.logging = logging

    def get_data(self):
        with serial.Serial('/dev/ttyUSB0', 9600, timeout=1) as ser:
            while True:
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
                self.__handle_commands(ser)

                time.sleep(1)

    def __handle_commands(self, ser):
        while not self.commands.empty():
            idx, value = self.commands.get()

            if self.data["outputs"][idx] is not value:
                ser.write(str.encode(str(idx)))
                self.logging.info("Arduino output %d set to %s", idx, value)
