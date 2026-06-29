import re
import socket
from typing import Pattern

PRIVATE_IP_RANGES: list[Pattern] = [
    re.compile(r'^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$'),
    re.compile(r'^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$'),
    re.compile(r'^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$'),
    re.compile(r'^192\.168\.\d{1,3}\.\d{1,3}$'),
    re.compile(r'^0\.0\.0\.0$'),
    re.compile(r'^::1$'),
    re.compile(r'^fc00:'),
    re.compile(r'^fe80:'),
]

def is_private_ip(host: str) -> bool:
    try:
        ip = socket.gethostbyname(host)
    except socket.gaierror:
        return False
    for pattern in PRIVATE_IP_RANGES:
        if pattern.match(ip):
            return True
    return False