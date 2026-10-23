"""Exercise the actual image without contacting news sources or model services."""
import http.cookiejar
import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main(image):
    name = "trendradar-web-smoke-" + uuid.uuid4().hex[:12]
    volume = name + "-data"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    cookies = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(NoRedirect, urllib.request.HTTPCookieProcessor(cookies))

    def request(path, data=None, csrf=None, method=None):
        headers = {"Origin": origin}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if csrf:
            headers["X-CSRF-Token"] = csrf
        req = urllib.request.Request(origin + path, method=method, headers=headers,
                                     data=json.dumps(data).encode() if data is not None else None)
        try:
            with client.open(req, timeout=3) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def wait():
        for _ in range(120):
            try:
                if request("/login")[0] == 200:
                    return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.5)
        raise RuntimeError("Container did not become ready")

    try:
        docker("volume", "create", volume)
        output = docker("run", "--rm", "-v", volume + ":/data", image,
                        "init", "--username", "smoke", "--generate-password")
        password = re.search(r"临时密码: (\S+)", output).group(1)
        docker("run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:18765",
               "-e", "TREND_RADAR_ORIGIN=" + origin, "-v", volume + ":/data", image)
        wait()
        assert docker("exec", name, "id", "-u") == "10001"
        docker("exec", name, "python", "/usr/local/bin/web-healthcheck.py")
        assert request("/")[0] == 303
        assert request("/api/status")[0] == 401
        status, text = request("/api/login", {"username": "smoke", "password": password})
        assert status == 200, text
        csrf = json.loads(text)["csrf"]
        status, html = request("/")
        assert status == 200 and "/assets/editor.js" in html
        for path in ("/assets/editor.css", "/editor-assets/script.js", "/api/status"):
            assert request(path)[0] == 200, path
        status, text = request("/api/config/frequency_words.txt")
        assert status == 200
        config = json.loads(text)
        updated = config["content"] + "\n# image persistence check\n"
        assert request("/api/config/frequency_words.txt", {"content": updated, "revision": config["revision"]},
                       csrf=csrf, method="PUT")[0] == 200
        docker("restart", name)
        wait()
        # New login after restart proves the administrator database persisted.
        cookies.clear()
        assert request("/api/login", {"username": "smoke", "password": password})[0] == 200
        assert json.loads(request("/api/config/frequency_words.txt")[1])["content"] == updated
        print("Image smoke test passed: non-root, authentication, editor assets and restart persistence")
    except Exception:
        subprocess.run(["docker", "logs", name], check=False)
        raise
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=False, stdout=subprocess.DEVNULL)
        subprocess.run(["docker", "volume", "rm", volume], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main(sys.argv[1])
