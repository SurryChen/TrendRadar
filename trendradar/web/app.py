"""Authenticated HTTP boundary; nothing from the repository is served by default."""
import asyncio
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Route
from starlette.exceptions import HTTPException

from .auth import AuthStore
from .runtime import Collector, FILES, Workspace

COOKIE = "trendradar_session"
STATIC = Path(__file__).parent / "static"


def create_app(data_dir, source_dir, origin="http://127.0.0.1:18765", secure_cookie=False):
    from urllib.parse import urlsplit
    parsed = urlsplit(origin)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("origin 必须是完整站点地址，不含路径、账号或查询参数")
    origin = origin.rstrip("/")
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1") and (parsed.scheme != "https" or not secure_cookie):
        raise ValueError("非本机访问必须配置 HTTPS origin 和 secure cookie")
    workspace = Workspace(data_dir, source_dir)
    auth = AuthStore(workspace.root / "auth.sqlite3")
    collector = Collector(workspace)

    @asynccontextmanager
    async def lifespan(app):
        # One process owns the runtime, including collection and config writes.
        import fcntl
        with (workspace.root / "server.lock").open("w") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise RuntimeError("此工作目录已有 Web 服务运行，只能启动一个 worker") from e
            yield
            if collector.task and not collector.task.done():
                collector.task.cancel()
                await collector.task

    async def body(request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "需要 JSON 请求")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 300_000:
                raise HTTPException(413, "请求过大")
        import json
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "JSON 格式有误")

    async def login(request):
        data = await body(request)
        result, error = await asyncio.to_thread(auth.login, data.get("username", ""), data.get("password", ""), request.client.host)
        if error:
            return JSONResponse({"error": "尝试次数过多，请 5 分钟后再试" if error == "rate" else "账号或密码不正确"}, status_code=429 if error == "rate" else 401)
        token, csrf = result
        old = request.cookies.get(COOKIE)
        if old:
            auth.logout(old)
        response = JSONResponse({"username": auth.username(), "csrf": csrf})
        response.set_cookie(COOKIE, token, max_age=12 * 3600, httponly=True, secure=secure_cookie, samesite="strict", path="/")
        return response

    async def session(request):
        return JSONResponse({"username": auth.username(), "csrf": request.state.csrf})

    async def logout(request):
        auth.logout(request.cookies.get(COOKIE, ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", secure=secure_cookie, httponly=True, samesite="strict")
        return response

    async def password(request):
        data = await body(request)
        await asyncio.to_thread(auth.change_password, data.get("old_password", ""), data.get("new_password", ""))
        return await logout(request)

    async def config(request):
        name = request.path_params["name"]
        if request.method == "GET":
            return JSONResponse(workspace.read(name))
        data = await body(request)
        # No configuration changes during a collection, including the gap before child startup.
        if collector.task and not collector.task.done():
            raise FileExistsError("采集中，请在任务结束后保存配置")
        async with workspace.lock:
            result = workspace.save(name, data.get("content"), data.get("revision"))
        return JSONResponse(result)

    async def collect(request):
        return JSONResponse(collector.start(), status_code=202)

    async def summary(request):
        import yaml
        data = yaml.safe_load(workspace.path("config.yaml").read_text())
        platforms = data.get("platforms", {})
        rss = data.get("rss", {})
        return JSONResponse({
            "platforms": sum(s.get("enabled", True) for s in platforms.get("sources", [])) if platforms.get("enabled", True) else 0,
            "rss": sum(s.get("enabled", True) for s in rss.get("feeds", [])) if rss.get("enabled", False) else 0,
        })

    async def status(request):
        root = workspace.root / "output/html"
        reports = []
        if root.exists():
            for p in sorted(root.rglob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True):
                resolved = p.resolve()
                if resolved.is_relative_to(root.resolve()) and p.is_file():
                    reports.append({"path": p.relative_to(root).as_posix(), "updated_at": p.stat().st_mtime})
                if len(reports) >= 30:
                    break
        return JSONResponse({"job": collector.status, "reports": reports, "log": collector.log_tail(), "files": FILES})

    async def report(request):
        root = (workspace.root / "output/html").resolve()
        path = (root / request.path_params["path"]).resolve()
        if not path.is_relative_to(root) or path.suffix != ".html" or not path.is_file():
            raise HTTPException(404)
        response = FileResponse(path, media_type="text/html")
        # Reports contain third-party headlines. Give scripts an opaque origin so they
        # cannot read admin APIs even if an upstream renderer ever misses escaping.
        response.headers["Content-Security-Policy"] = "sandbox allow-scripts allow-popups; default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data: https:; font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        return response

    async def page(request):
        return FileResponse(STATIC / "index.html")

    async def asset(request):
        name = request.path_params["name"]
        if name not in ("app.js", "style.css"):
            raise HTTPException(404)
        return FileResponse(STATIC / name)

    async def error(request, exception):
        return JSONResponse({"error": str(exception)}, status_code=409 if isinstance(exception, FileExistsError) else 400)

    async def http_error(request, exception):
        return JSONResponse({"error": str(exception.detail)}, status_code=exception.status_code, headers=exception.headers)

    app = Starlette(routes=[
        Route("/login", page), Route("/", page),
        Route("/assets/{name}", asset),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/session", session), Route("/api/logout", logout, methods=["POST"]),
        Route("/api/password", password, methods=["POST"]),
        Route("/api/config/{name}", config, methods=["GET", "PUT"]),
        Route("/api/summary", summary), Route("/api/status", status), Route("/api/collect", collect, methods=["POST"]),
        Route("/reports/{path:path}", report),
    ], lifespan=lifespan, exception_handlers={ValueError: error, FileExistsError: error, HTTPException: http_error})
    app.state.auth, app.state.workspace, app.state.collector = auth, workspace, collector

    async def boundary(request: Request, call_next):
        path = request.url.path
        public = path == "/login" or path in ("/assets/app.js", "/assets/style.css", "/api/login")
        csrf = auth.session(request.cookies.get(COOKIE, ""))
        request.state.csrf = csrf
        if not public and not csrf:
            response = JSONResponse({"error": "请先登录"}, status_code=401) if path.startswith("/api/") else RedirectResponse("/login", status_code=303)
        elif request.method not in ("GET", "HEAD", "OPTIONS") and (
            request.headers.get("origin") != origin or
            (path != "/api/login" and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf or "!"))
        ):
            response = JSONResponse({"error": "请求验证失败，请刷新后重试"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=boundary)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[parsed.hostname])
    return app
