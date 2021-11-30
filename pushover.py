import time
import threading
import http.client, urllib

class Pushover:
    def __init__(self, token, user):
        self.token = token
        self.user = user

    def _push(self, message, priority=0, data={}):
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
                         "message": message,
                         "timestamp": time.time(),
                         "sound": "gamelan"
                     } | data), {"Content-type": "application/x-www-form-urlencoded"})
        conn.getresponse()

    def push(self, message, priority=0, data={}):
        threading.Thread(target=self._push, args=(message, priority, data,)).start()
