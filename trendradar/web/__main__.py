"""Run with python -m trendradar.web init|serve."""
import argparse
import getpass
import secrets
from pathlib import Path

from .auth import AuthStore
from .runtime import Workspace


def main():
    parser = argparse.ArgumentParser(description="TrendRadar 登录管理后台")
    parser.add_argument("--data-dir", type=Path, default=Path(".web-data"))
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="仅在终端初始化管理员")
    init.add_argument("--username", default="admin")
    init.add_argument("--generate-password", action="store_true")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=18765)
    serve.add_argument("--origin", help="浏览器访问的完整源地址，公开部署必须为 HTTPS")
    serve.add_argument("--secure-cookie", action="store_true")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[2]
    workspace = Workspace(args.data_dir, source)
    auth = AuthStore(workspace.root / "auth.sqlite3")
    if args.command == "init":
        password = secrets.token_urlsafe(18) if args.generate_password else getpass.getpass("管理员密码（至少 12 字符）: ")
        if not args.generate_password and password != getpass.getpass("再次输入密码: "):
            parser.error("两次密码不一致")
        try:
            auth.initialize(args.username, password)
        except ValueError as e:
            parser.error(str(e))
        workspace.initialize_config()
        print(f"管理员已创建: {args.username}\n数据目录: {workspace.root}")
        if args.generate_password:
            print(f"临时密码: {password}\n请登录后更改密码。")
        return
    if not auth.username():
        parser.error("请先执行 python -m trendradar.web init 创建管理员")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.origin:
        parser.error("非本机监听必须显式设置 HTTPS --origin 和 --secure-cookie")
    import uvicorn
    from .app import create_app
    origin = args.origin or f"http://{'[' + args.host + ']' if ':' in args.host else args.host}:{args.port}"
    app = create_app(workspace.root, source, origin=origin, secure_cookie=args.secure_cookie)
    print(f"管理后台: {origin}（单 worker）")
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False, workers=1)


if __name__ == "__main__":
    main()
