import time
import threading
import http.client
import urllib.parse


class Pushover:
    def __init__(self, token: str, user: str):
        self.token = token
        self.user = user

    def _push(self, title:str, message: str, priority: int, data: dict) -> None:
        if priority == 2:
            data = {
                "sound": "alien",
                "priority": 2,
                "retry": 30,
                "expire": 3600
            }

        conn = http.client.HTTPSConnection("api.pushover.net:443")
        conn.request("POST", "/1/messages.json",
                     urllib.parse.urlencode({
                         "token": self.token,
                         "user": self.user,
                         "title": title,
                         "message": message,
                         "timestamp": time.time(),
                         "sound": "gamelan"
                     } | data), {"Content-type": "application/x-www-form-urlencoded"})
        conn.getresponse()

    def push(self, title: str, message: str, priority: int = 0, data: dict = None) -> None:
        if data is None:
            data = {}

        threading.Thread(target=self._push, args=(title, message, priority, data,)).start()
