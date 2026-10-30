import argparse
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import secrets
import threading
import urllib.request
import webbrowser

import uvicorn

from . import __version__
from .server import create_app


def main():
    parser = argparse.ArgumentParser(description="DevToolNext 本地串口工作台")
    parser.add_argument("--host", default="127.0.0.1", help="默认仅本机；局域网试用可设 0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=Path(
        os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "DevToolNext")
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(args.data_dir / "application.log", maxBytes=1_000_000,
                                  backupCount=3, encoding="utf-8")
    logging.basicConfig(handlers=[handler], level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    control, view = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    app = create_app(args.data_dir, control, view)
    url = f"http://127.0.0.1:{args.port}"
    logging.getLogger(__name__).info("启动 DevToolNext %s host=%s port=%s data=%s",
                                     __version__, args.host, args.port, args.data_dir)
    print(f"DevToolNext 数据目录：{args.data_dir}")
    print(f"本机控制入口：{url}/#token={control}")
    if args.host != "127.0.0.1":
        print("局域网试用已开启：HTTP 未加密，仅用于受信网络；不要转发到公网。")
        print(f"只读访问：将以下地址的 <本机IP> 替换为服务端地址：http://<本机IP>:{args.port}/#token={view}")

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
