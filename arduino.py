import serial
import time
import queue
import threading
import logging

'''
Inputs:
1. N/C
2. Siren relay
3. Siren actual
4. Water valve switch
5. Water alarm reset button

Outputs:
1. Siren block relay
2. Charger relay (NC)
3. Water valve relay
4. Dish washer relay (NC)
5. Aux 1 (Outdoor lights relay 1)
6. Aux 2 (Outdoor lights relay 2)
7. N/C

Note:
Inputs and outputs are read starting at 0, while outputs are changed starting at 1.
Meaning output 1 is read as output[0] but changed with "o,1,x".
'''


class Arduino:
    def __init__(self):
        self.data = {}
        self.commands = queue.Queue()
        self.voltage1 = []
        self.voltage2 = []
        self.temperature = []
        self.timestamp = time.time()
        self.data_ready = threading.Event()

    def get_data(self) -> None:
        with serial.Serial('/dev/ttyUSB0', 9600, timeout=1) as ser:
            while True:
                self.data_ready.clear()
                # start_time = time.time()
                self._handle_commands(ser)

                ser.write(str.encode("s\n"))
                line = ser.readline()   # read a '\n' terminated line
                received = line.decode('utf-8').strip()
                if received == "":
                    continue

                # print(received)
                received = received.split("|")

                # Factors
                # voltage1: 12.004 / 2.975
                # voltage2: 12.004 / 2.979

                ai_voltage = 4.705 / 1023
                ai_factor = [4.03495798319327731092, 4.02954011413225914736]
                ai_samples = 10

                self.voltage1.append(int(received[0]) * ai_voltage * ai_factor[0])
                self.voltage2.append(int(received[1]) * ai_voltage * ai_factor[1])
                self.temperature.append(float(received[2]))

                if len(self.voltage1) > ai_samples:
                    self.voltage1.pop(0)
                if len(self.voltage2) > ai_samples:
                    self.voltage2.pop(0)
                if len(self.temperature) > ai_samples:
                    self.temperature.pop(0)

                data = {
                    "voltage1": round(sum(self.voltage1) / len(self.voltage1), 2),
                    "voltage2": round(sum(self.voltage2) / len(self.voltage2), 2),
                    "temperature": round(sum(self.temperature) / len(self.temperature), 2),
                    "inputs": [not bool(int(received[3]) & (1 << n)) for n in range(5)],
                    "outputs": [bool(int(received[4]) & (1 << n)) for n in range(7)]
                }

                self.data = data
                self.timestamp = time.time()
                self.data_ready.set()
                # print(time.time() - start_time)

    def _handle_commands(self, ser: serial.Serial) -> None:
        while not self.commands.empty():
            idx, value = self.commands.get()
            value_int = int(value is True)

            ser.write(str.encode(f"o,{idx},{value_int}\n"))
            logging.info("Arduino output %d set to %s", idx, value)
            self.commands.task_done()
