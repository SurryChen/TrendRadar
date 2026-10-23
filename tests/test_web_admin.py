"""Security and integration tests; no network, notifications or model calls."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from starlette.testclient import TestClient

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from trendradar.web.app import COOKIE, create_app
from trendradar.web.auth import AuthStore

SOURCE = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:18765"
PASSWORD = "test-private-password-2026"


class WebAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_app(self.tmp.name, SOURCE)
        self.auth = self.app.state.auth
        self.auth.initialize("admin", PASSWORD)
        self.app.state.workspace.initialize_config()
        self.client = self.enterContext(TestClient(self.app, base_url=ORIGIN))

    def login(self):
        result = self.client.post("/api/login", json={"username": "admin", "password": PASSWORD}, headers={"Origin": ORIGIN})
        self.assertEqual(result.status_code, 200, result.text)
        self.headers = {"Origin": ORIGIN, "X-CSRF-Token": result.json()["csrf"]}
        return result

    def test_unauthenticated_boundaries(self):
        for path in ("/", "/reports/latest/current.html", "/config/config.yaml", "/editor/"):
            self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 303)
        for path in ("/api/session", "/api/config/config.yaml", "/api/status", "/api/summary"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post("/api/collect", json={}).status_code, 401)
        self.assertEqual(self.client.get("/login").status_code, 200)

    def test_upstream_editor_and_authenticated_assets(self):
        for path in ('/editor-assets/script.js', '/assets/editor.js'):
            self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 303)
        self.login()
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="yaml-editor"', response.text)
        self.assertIn('id="config-panel"', response.text)
        self.assertIn('/assets/editor.js', response.text)
        self.assertNotIn('id="workspace"', response.text)
        self.assertIn('cdn.tailwindcss.com', response.headers['content-security-policy'])
        self.assertEqual(self.client.get('/editor/').text, response.text)
        original = SOURCE / 'docs/assets/script.js'
        self.assertEqual(self.client.get('/editor-assets/script.js').content, original.read_bytes())
        self.assertEqual(self.client.get('/editor-assets/i18n.js').status_code, 200)
        self.assertEqual(self.client.get('/editor-assets/auth.sqlite3').status_code, 404)
        self.assertIn('const localStorage', self.client.get('/assets/editor-storage.js').text)

    def test_origin_csrf_cookie_and_logout(self):
        credentials = {"username": "admin", "password": PASSWORD}
        for origin in (None, "https://other.example"):
            headers = {"Origin": origin} if origin else {}
            self.assertEqual(self.client.post("/api/login", json=credentials, headers=headers).status_code, 403)
        login = self.login()
        cookie = login.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertNotIn("Secure", cookie)
        token = self.client.cookies[COOKIE]
        self.assertEqual(self.client.post("/api/logout", json={}, headers={"Origin": ORIGIN}).status_code, 403)
        self.assertEqual(self.client.post("/api/logout", json={}, headers=self.headers).status_code, 200)
        self.assertIsNone(self.auth.session(token))
        self.assertEqual(self.client.get("/api/status").status_code, 401)

    def test_password_change_revokes_all_sessions(self):
        self.login()
        first = self.client.cookies[COOKIE]
        second, _ = self.auth.login("admin", PASSWORD, "test")
        bad = self.client.post("/api/password", json={"old_password":"wrong", "new_password":"new-strong-password"}, headers=self.headers)
        self.assertEqual(bad.status_code, 400)
        response = self.client.post("/api/password", json={"old_password":PASSWORD, "new_password":"new-strong-password"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.auth.session(first))
        self.assertIsNone(self.auth.session(second[0]))
        result, _ = self.auth.login("admin", PASSWORD, "test")
        self.assertIsNone(result)
        result, _ = self.auth.login("admin", "new-strong-password", "test")
        self.assertIsNotNone(result)

    def test_expired_sessions_and_rate_limit(self):
        self.login()
        with self.auth.connect() as db:
            db.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
        self.assertEqual(self.client.get("/api/session").status_code, 401)
        # Avoid ten expensive real hashes while testing persisted throttling.
        with patch.object(AuthStore, "verify", return_value=False):
            for _ in range(10):
                _, reason = self.auth.login("admin", "wrong", "test")
                self.assertEqual(reason, "invalid")
        _, reason = self.auth.login("admin", PASSWORD, "another-client")
        self.assertEqual(reason, "rate")
        with self.auth.connect() as db:
            db.execute("UPDATE attempts SET until=?", (time.time()-1,))
        result, _ = self.auth.login("admin", PASSWORD, "test")
        self.assertIsNotNone(result)

    def test_config_persistence_validation_conflicts_and_allowlist(self):
        self.login()
        original = self.client.get("/api/config/config.yaml").json()
        change = {"content":original["content"]+"\n# saved via web\n", "revision":original["revision"]}
        result = self.client.put("/api/config/config.yaml", json=change, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn("# saved via web", self.app.state.workspace.path("config.yaml").read_text())
        self.assertEqual(len(list((Path(self.tmp.name)/"backups").iterdir())), 1)
        self.assertEqual(self.client.put("/api/config/config.yaml", json=change, headers=self.headers).status_code, 409)
        change["revision"] = result.json()["revision"]
        for invalid in ("platforms: [oops]", "[]", "report:\n  mode: wrong", "storage:\n  backend: remote", "x: ["):
            change["content"] = invalid
            self.assertEqual(self.client.put("/api/config/config.yaml", json=change, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get("/api/config/auth.sqlite3").status_code, 400)
        self.assertEqual(self.client.get("/api/config/%2e%2e%2fauth.sqlite3").status_code, 404)
        self.assertIn("# saved via web", self.app.state.workspace.path("config.yaml").read_text())
        self.assertEqual(self.client.put("/api/config/config.yaml", content=b'x'*300001, headers={**self.headers,"Content-Type":"application/json"}).status_code, 413)

    def test_reports_sandbox_and_path_traversal(self):
        self.login()
        root=Path(self.tmp.name)/"output/html/latest"
        root.mkdir(parents=True)
        (root/"current.html").write_text("<h1>Collected news</h1>")
        (root/"escape.html").symlink_to(SOURCE/"README.md")
        self.assertEqual(self.client.get("/reports/latest/current.html").status_code, 200)
        self.assertIn("sandbox allow-scripts", self.client.get("/reports/latest/current.html").headers["content-security-policy"])
        for path in ("/reports/latest/escape.html", "/reports/%2e%2e%2fauth.sqlite3", "/auth.sqlite3", "/config/config.yaml", "/.git/config"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.client.get("/api/status").json()["reports"][0]["path"], "latest/current.html")
        self.assertEqual(self.client.get("/", headers={"Host":"attacker.example"}).status_code, 400)

    def test_single_job_and_configuration_lock(self):
        self.login()
        async def slow_run():
            await asyncio.sleep(30)
        with patch.object(self.app.state.collector, "run", side_effect=slow_run):
            self.assertEqual(self.client.post("/api/collect", json={}, headers=self.headers).status_code, 202)
            self.assertEqual(self.client.post("/api/collect", json={}, headers=self.headers).status_code, 409)
            item=self.client.get("/api/config/frequency_words.txt").json()
            self.assertEqual(self.client.put("/api/config/frequency_words.txt",json=item,headers=self.headers).status_code,409)
            self.client.portal.call(self.app.state.collector.task.cancel)
            # A mocked coroutine has no real child process to clean up.
            async def reap():
                try: await self.app.state.collector.task
                except asyncio.CancelledError: pass
            self.client.portal.call(reap)

    def test_initialization_is_terminal_only_and_secrets_are_hashed(self):
        self.assertEqual(self.client.post("/api/setup", json={}, headers={"Origin": ORIGIN}).status_code, 401)
        with self.assertRaises(ValueError):
            self.auth.initialize("another", PASSWORD)
        self.assertNotIn(PASSWORD.encode(), (Path(self.tmp.name)/"auth.sqlite3").read_bytes())
        data=yaml.safe_load(self.app.state.workspace.path("config.yaml").read_text())
        self.assertFalse(data["notification"]["enabled"])
        self.assertFalse(data["ai_analysis"]["enabled"])
        self.assertFalse(data["ai_translation"]["enabled"])
        self.assertEqual(os.stat(Path(self.tmp.name)/"auth.sqlite3").st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            create_app(self.tmp.name, SOURCE, origin="http://public.example")
        public=create_app(self.tmp.name, SOURCE, origin="https://public.example",secure_cookie=True)
        with tempfile.TemporaryDirectory() as alternate:
            public=create_app(alternate, SOURCE, origin="https://public.example",secure_cookie=True)
            public.state.auth.initialize("admin", PASSWORD)
            client=self.enterContext(TestClient(public,base_url="https://public.example"))
            response=client.post('/api/login',json={"username":"admin","password":PASSWORD},headers={"Origin":"https://public.example"})
            self.assertIn('Secure',response.headers['set-cookie'])


if __name__ == '__main__':
    unittest.main()
