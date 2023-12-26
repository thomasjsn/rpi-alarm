import logging
import socket
import urllib.request
import threading


class HealthChecks:
    def __init__(self, hc_uuid: str):
        self.hc_uuid = hc_uuid
        self.lock = threading.Lock()

    def ping(self, start: bool = False) -> bool:
        if self.hc_uuid is None:
            return False

        ping_url = f"https://hc-ping.com/{self.hc_uuid}"

        if start:
            ping_url += "/start"

        try:
            urllib.request.urlopen(ping_url, timeout=10)
            return True

        except socket.error as e:
            logging.error("Healthchecks returned error: %s", e)
            return False

    def start(self) -> bool:
        ping_result = self.ping(True)

        if ping_result:
            self.lock.acquire()

        return ping_result

    def stop(self) -> bool:
        ping_result = self.ping()

        if ping_result:
            self.lock.release()

        return ping_result
