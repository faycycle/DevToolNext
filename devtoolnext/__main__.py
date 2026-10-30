import argparse
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import secrets
import sys
import threading
import urllib.request
import webbrowser

import uvicorn

from . import __version__
from .server import create_app


def _load_tokens(data_dir: Path, rotate: bool = False):
    """Keep the tokens stable across restarts so saved MCP configs keep working."""
    env_control = os.environ.get("DEVTOOLNEXT_CONTROL_TOKEN", "")
    env_view = os.environ.get("DEVTOOLNEXT_VIEW_TOKEN", "")
    if env_control and env_view:
        return env_control, env_view
    path = data_dir / "tokens.json"
    if not rotate and path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("control") and saved.get("view"):
                return saved["control"], saved["view"]
        except (OSError, ValueError):
            pass
    control, view = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    try:
        path.write_text(json.dumps({"control": control, "view": view}, indent=2), encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError:
        logging.getLogger(__name__).warning("无法保存访问密钥到 %s", path)
    return control, view


def main():
    # Redirected stdout defaults to the ANSI code page on Windows; keep logs readable.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="DevToolNext 本地串口工作台")
    parser.add_argument("--host", default="127.0.0.1", help="默认仅本机；局域网试用可设 0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--mcp-stdio", action="store_true",
                        help="以 MCP stdio 方式运行，桥接到已启动的本机实例")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8765",
                        help="MCP stdio 模式要连接的本机地址")
    parser.add_argument("--mcp-token", default=os.environ.get("DEVTOOLNEXT_TOKEN", ""),
                        help="MCP stdio 模式使用的控制密钥")
    parser.add_argument("--rotate-tokens", action="store_true",
                        help="重新生成访问密钥（会让旧的浏览器地址和 MCP 配置失效）")
    parser.add_argument("--data-dir", type=Path, default=Path(
        os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "DevToolNext")
    args = parser.parse_args()
    if args.mcp_stdio:
        from .mcp_stdio import run
        run(args.mcp_url, args.mcp_token)
        return
    args.data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(args.data_dir / "application.log", maxBytes=1_000_000,
                                  backupCount=3, encoding="utf-8")
    logging.basicConfig(handlers=[handler], level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    control, view = _load_tokens(args.data_dir, args.rotate_tokens)
    app = create_app(args.data_dir, control, view)
    url = f"http://127.0.0.1:{args.port}"
    logging.getLogger(__name__).info("启动 DevToolNext %s host=%s port=%s data=%s",
                                     __version__, args.host, args.port, args.data_dir)
    print(f"DevToolNext 数据目录：{args.data_dir}", flush=True)
    print(f"本机控制入口：{url}/#token={control}", flush=True)
    if args.host != "127.0.0.1":
        print("局域网试用已开启：HTTP 未加密，仅用于受信网络；不要转发到公网。", flush=True)
        print(f"只读访问：将以下地址的 <本机IP> 替换为服务端地址：http://<本机IP>:{args.port}/#token={view}", flush=True)

    if not args.no_browser:
        def open_when_ready():
            import time
            for _ in range(50):
                try:
                    with urllib.request.urlopen(url, timeout=0.5) as response:
                        if response.status == 200:
                            webbrowser.open(f"{url}/#token={control}")
                            return
                except OSError:
                    time.sleep(0.1)
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(app, host=args.host, port=args.port, access_log=False,
                log_config=None)


if __name__ == "__main__":
    main()
