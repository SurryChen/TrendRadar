"""Probe the public login page with the configured external Host header."""
import os
import urllib.request
from urllib.parse import urlsplit

origin = os.environ.get("TREND_RADAR_ORIGIN", "http://127.0.0.1:18765")
request = urllib.request.Request(
    "http://127.0.0.1:18765/login", headers={"Host": urlsplit(origin).netloc}
)
with urllib.request.urlopen(request, timeout=4) as response:
    if response.status != 200:
        raise SystemExit(1)
