"""Isolated configuration files and one bounded collection subprocess."""
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import yaml

FILES = ("config.yaml", "frequency_words.txt", "timeline.yaml", "ai_interests.txt")


def atomic_write(path, content):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as f:
            os.chmod(temporary, 0o600)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Workspace:
    def __init__(self, root, source):
        self.root, self.source = Path(root).resolve(), Path(source).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "config").mkdir(exist_ok=True, mode=0o700)
        (self.root / "backups").mkdir(exist_ok=True, mode=0o700)
        for original in (self.source / "config").glob("*"):
            target = self.root / "config" / original.name
            if original.is_file() and not target.exists():
                shutil.copyfile(original, target)
                target.chmod(0o600)
        self.lock = asyncio.Lock()

    def initialize_config(self):
        # Called explicitly only at first initialization; no external notifications/AI by default.
        p = self.root / "config/config.yaml"
        data = yaml.safe_load(p.read_text())
        for key in ("notification", "ai_analysis", "ai_translation", "schedule"):
            data.setdefault(key, {})["enabled"] = False
        data.setdefault("filter", {})["method"] = "keyword"
        data.setdefault("app", {})["show_version_update"] = False
        data.setdefault("storage", {})["backend"] = "local"
        data["storage"].setdefault("local", {})["data_dir"] = "output"
        atomic_write(p, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))

    def path(self, name):
        if name not in FILES:
            raise ValueError("不支持的配置文件")
        return self.root / "config" / name

    def read(self, name):
        content = self.path(name).read_text(encoding="utf-8")
        return {"name": name, "content": content, "revision": hashlib.sha256(content.encode()).hexdigest()}

    def save(self, name, content, revision):
        if not isinstance(content, str) or len(content.encode()) > 256_000:
            raise ValueError("配置内容过长")
        if self.read(name)["revision"] != revision:
            raise FileExistsError("配置已被其他页面修改，请重新读取后再保存")
        if name.endswith(".yaml"):
            try:
                data = yaml.safe_load(content)
            except yaml.YAMLError as e:
                raise ValueError("YAML 格式有误，请检查缩进与符号") from e
            if not isinstance(data, dict):
                raise ValueError("YAML 顶层必须是对象")
            # Exercise the upstream loaders using an isolated candidate config directory.
            import tempfile
            from contextlib import redirect_stdout
            from io import StringIO
            from trendradar.core.loader import load_config
            with tempfile.TemporaryDirectory(dir=self.root) as folder:
                candidate = Path(folder)
                for original in (self.root / "config").iterdir():
                    if original.is_file():
                        shutil.copyfile(original, candidate / original.name)
                (candidate / name).write_text(content)
                try:
                    with redirect_stdout(StringIO()):
                        loaded = load_config(str(candidate / "config.yaml"))
                        from datetime import datetime
                        from trendradar.core.scheduler import Scheduler
                        Scheduler(loaded["SCHEDULE"], loaded["_TIMELINE_DATA"], None, datetime.now)
                except Exception as e:
                    raise ValueError("配置无法被 TrendRadar 加载，请检查字段类型与时间线配置") from e
            if name == "config.yaml":
                for key in ("app", "platforms", "rss", "report", "notification", "storage", "ai"):
                    if key in data and not isinstance(data[key], dict):
                        raise ValueError(f"{key} 必须是对象")
                if data.get("report", {}).get("mode", "daily") not in ("daily", "current", "incremental"):
                    raise ValueError("报告模式只能为 daily / current / incremental")
                for key in ("platforms", "rss"):
                    section = data.get(key, {})
                    sources = section.get("sources", section.get("feeds", []))
                    if not isinstance(sources, list) or any(not isinstance(s, dict) for s in sources):
                        raise ValueError(f"{key} 的来源列表格式有误")
                # The web workspace always stores local outputs under its own directory.
                storage = data.get("storage", {})
                if storage.get("backend", "local") != "local" or storage.get("local", {}).get("data_dir", "output") != "output":
                    raise ValueError("Web 版目前只支持工作目录中的本地 output 存储")
        target = self.path(name)
        atomic_write(self.root / "backups" / f"{time.time_ns()}-{name}", target.read_text())
        atomic_write(target, content)
        return self.read(name)


class Collector:
    def __init__(self, workspace):
        self.workspace = workspace
        self.task = None
        self.process = None
        self.status_path = workspace.root / "job.json"
        self.status = {"state": "idle", "message": "尚未开始采集"}
        if self.status_path.exists():
            self.status = json.loads(self.status_path.read_text())
            if self.status["state"] == "running":
                self.status.update(state="interrupted", message="服务重启，上一任务已中断")
                self.persist()

    def persist(self):
        atomic_write(self.status_path, json.dumps(self.status, ensure_ascii=False))

    def start(self):
        if self.task and not self.task.done():
            raise FileExistsError("已有采集任务正在运行")
        self.status = {"state": "running", "started_at": time.time(), "message": "采集中"}
        self.persist()
        self.task = asyncio.create_task(self.run())
        return self.status

    async def run(self):
        # Separate cwd keeps the upstream engine's relative paths out of the source checkout.
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "SYSTEMROOT")}
        env.update(LITELLM_LOCAL_MODEL_COST_MAP="True", PYTHONPATH=str(self.workspace.source), PYTHONUNBUFFERED="1", BROWSER="true", CONFIG_PATH="config/config.yaml")
        try:
            async with self.workspace.lock:
                log = self.workspace.root / "collection.log"
                with log.open("w") as output:
                    os.chmod(log, 0o600)
                    self.process = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "trendradar", cwd=self.workspace.root,
                        env=env, stdout=output, stderr=asyncio.subprocess.STDOUT)
                    try:
                        code = await asyncio.wait_for(self.process.wait(), timeout=900)
                    except asyncio.TimeoutError:
                        self.process.kill()
                        await self.process.wait()
                        self.status.update(state="failed", message="采集超过 15 分钟，已停止")
                    else:
                        self.status.update(state="succeeded" if code == 0 else "failed", exit_code=code,
                                           message="采集流程完成，请查看报告及来源结果" if code == 0 else "采集失败，请查看日志")
        except asyncio.CancelledError:
            if self.process and self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
            self.status.update(state="interrupted", message="服务关闭，采集已停止")
        except Exception:
            self.status.update(state="failed", message="采集进程启动失败")
        finally:
            self.status["finished_at"] = time.time()
            self.persist()

    def log_tail(self):
        log = self.workspace.root / "collection.log"
        if not log.exists():
            return ""
        with log.open("rb") as f:
            f.seek(max(0, log.stat().st_size - 16000))
            return f.read().decode("utf-8", errors="replace")
