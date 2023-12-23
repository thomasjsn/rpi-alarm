import socket
import urllib.request


def ping(uuid: str) -> bool:
    try:
        urllib.request.urlopen(f"https://hc-ping.com/{uuid}", timeout=10)
        return True

    except socket.error:
        return False
