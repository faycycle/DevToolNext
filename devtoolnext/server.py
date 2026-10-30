import base64
import binascii
import hmac
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core import ConnectionManager, DeviceService
from .mcp import McpHandler
from . import __version__
from .storage import SessionStore

log = logging.getLogger(__name__)


class ConnectRequest(BaseModel):
    type: Literal["serial", "telnet", "ssh"] = "serial"
    port: str = Field(min_length=1, max_length=256)
    baudrate: int = Field(default=115200, ge=50, le=4000000)
    bytesize: Literal[5, 6, 7, 8] = 8
    parity: Literal["N", "E", "O", "M", "S"] = "N"
    stopbits: Literal[1, 1.5, 2] = 1
    rtscts: bool = False
    tcp_port: int = Field(default=0, ge=0, le=65535)
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=256)
    keyfile: str = Field(default="", max_length=260)


class SendRequest(BaseModel):
    data: str = Field(min_length=1, max_length=16384)
    format: Literal["text", "hex"] = "text"
    encoding: Literal["utf-8", "gb18030", "ascii"] = "utf-8"
    ending: Literal["", "\n", "\r", "\r\n"] = "\n"


class PresetRequest(BaseModel):
    name: str = Field(default="", max_length=60)
    config: ConnectRequest


class SettingsRequest(BaseModel):
    output_dir: str | None = Field(default=None, max_length=260)
    keep_raw: bool | None = None
    mcp_enabled: bool | None = None


class LinesRequest(BaseModel):
    dtr: bool | None = None
    rts: bool | None = None


def _send_data(service: DeviceService, body: SendRequest):
    try:
        if body.format == "hex":
            try:
                data = bytes.fromhex(body.data)
            except (ValueError, binascii.Error) as exc:
                raise HTTPException(400, f"HEX 内容无效：{exc}。只允许 0-9 / A-F 与空格") from exc
        else:
            # HEX frames are sent verbatim; the line ending only applies to text.
            data = (body.data + body.ending).encode(body.encoding)
        return service.send(data)
    except HTTPException:
        raise
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc


def create_app(data_dir: Path, control_token=None, view_token=None, service=None):
    control_token = control_token or secrets.token_urlsafe(32)
    view_token = view_token or secrets.token_urlsafe(32)
    store = service.store if service is not None else SessionStore(data_dir)
    manager = ConnectionManager(store)
    service = manager.default

    @asynccontextmanager
    async def lifespan(app):
        yield
        manager.close_all()
        store.close()

    app = FastAPI(title="DevToolNext", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.service = service
    app.state.manager = manager
    app.state.control_token = control_token
    app.state.view_token = view_token

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        return response

    def role_for(supplied: str) -> str:
        if supplied and hmac.compare_digest(supplied.encode(), control_token.encode()):
            return "control"
        if supplied and hmac.compare_digest(supplied.encode(), view_token.encode()):
            return "view"
        raise HTTPException(401, "访问密钥无效")

    def authorize(request: Request, authorization: str = Header(default="")):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "不允许跨来源请求")
        return role_for(authorization.removeprefix("Bearer "))

    def controller(role=Depends(authorize)):
        if role != "control":
            raise HTTPException(403, "当前为只读权限")

    def connection_or_404(cid: str) -> DeviceService:
        found = manager.get(cid)
        if found is None:
            raise HTTPException(404, "连接不存在")
        return found

    mcp_handler = McpHandler(manager, store, __version__)

    @app.post("/mcp")
    async def mcp_endpoint(request: Request, authorization: str = Header(default=""),
                           token: str = Query(default="")):
        """MCP over JSON-RPC; header or ?token= keeps header-less clients working."""
        if store.get_setting("mcp_enabled", "1") == "0":
            raise HTTPException(404, "MCP 接口已关闭，可在界面里开启")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "不允许跨来源请求")
        role = role_for(authorization.removeprefix("Bearer ") or token)
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(400, "请求体必须是 JSON-RPC 对象") from exc
        batch = isinstance(payload, list)
        messages = payload if batch else [payload]
        answers = [answer for answer in (mcp_handler.handle(item, role) for item in messages)
                   if answer is not None]
        if not answers:
            return Response(status_code=202)
        session = request.headers.get("mcp-session-id") or secrets.token_hex(16)
        return JSONResponse(answers if batch else answers[0],
                            headers={"Mcp-Session-Id": session})

    @app.get("/api/status")
    def status(role=Depends(authorize)):
        return {**service.status(), "role": role, "version": __version__,
                "presets": store.presets()}

    @app.get("/api/connections")
    def connections(role=Depends(authorize)):
        return {"role": role, "version": __version__,
                "connections": manager.list(), "presets": store.presets()}

    @app.post("/api/connections", dependencies=[Depends(controller)])
    def open_connection(body: ConnectRequest):
        config = body.model_dump()
        created = DeviceService(store)
        try:
            created.connect(config)
        except Exception as exc:
            raise HTTPException(409, str(exc)) from exc
        manager.add(created)
        try:
            store.save_preset(config)
        except Exception:
            log.exception("保存连接预设失败")
        return manager.describe(created)

    @app.delete("/api/connections/{cid}", dependencies=[Depends(controller)])
    def close_connection(cid: str):
        found = connection_or_404(cid)
        try:
            found.disconnect()
        except Exception as exc:
            raise HTTPException(409, str(exc)) from exc
        manager.remove(cid)
        return {"closed": cid}

    @app.post("/api/connections/{cid}/send", dependencies=[Depends(controller)])
    def send_on(cid: str, body: SendRequest):
        return _send_data(connection_or_404(cid), body)

    @app.post("/api/connections/{cid}/lines", dependencies=[Depends(controller)])
    def lines_on(cid: str, body: LinesRequest):
        found = connection_or_404(cid)
        try:
            return found.set_lines(body.dtr, body.rts)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/ports", dependencies=[Depends(authorize)])
    def ports():
        return service.plugin.discover()

    @app.get("/api/presets", dependencies=[Depends(authorize)])
    def presets():
        return store.presets()

    @app.get("/api/settings", dependencies=[Depends(authorize)])
    def read_settings():
        return {"output_dir": str(store.output_dir()),
                "data_dir": str(store.root),
                "keep_raw": store.get_setting("keep_raw", "1") != "0",
                "mcp_enabled": store.get_setting("mcp_enabled", "1") != "0",
                "log_file": service.status().get("log_file")}

    @app.post("/api/settings", dependencies=[Depends(controller)])
    def save_settings(body: SettingsRequest):
        result = {}
        if body.output_dir:
            try:
                result["output_dir"] = str(store.set_output_dir(body.output_dir))
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        if body.keep_raw is not None:
            store.set_setting("keep_raw", "1" if body.keep_raw else "0")
            result["keep_raw"] = body.keep_raw
        if body.mcp_enabled is not None:
            store.set_setting("mcp_enabled", "1" if body.mcp_enabled else "0")
            result["mcp_enabled"] = body.mcp_enabled
        return result

    @app.post("/api/reveal", dependencies=[Depends(controller)])
    def reveal_output_dir():
        path = store.output_dir()
        if os.name != "nt":
            raise HTTPException(409, "当前系统不能自动打开目录")
        try:
            os.startfile(str(path))
        except OSError as exc:
            raise HTTPException(409, f"无法打开目录：{exc}") from exc
        return {"opened": str(path)}

    @app.post("/api/presets", dependencies=[Depends(controller)])
    def save_preset(body: PresetRequest):
        pid = store.save_preset(body.config.model_dump(), body.name)
        return next((p for p in store.presets(50) if p["id"] == pid), {"id": pid})

    @app.delete("/api/presets/{pid}", dependencies=[Depends(controller)])
    def delete_preset(pid: str):
        if not store.delete_preset(pid):
            raise HTTPException(404, "预设不存在")
        return {"deleted": pid}

    # ---- legacy single-connection API (kept for scripts and the bundled tests) ----
    @app.post("/api/connect", dependencies=[Depends(controller)])
    def connect(body: ConnectRequest):
        config = body.model_dump()
        try:
            connected = service.connect(config)
        except Exception as exc:
            raise HTTPException(409, str(exc)) from exc
        try:
            store.save_preset(config)
        except Exception:
            log.exception("保存连接预设失败")
        return connected

    @app.post("/api/disconnect", dependencies=[Depends(controller)])
    def disconnect():
        try:
            return service.disconnect()
        except Exception as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/lines", dependencies=[Depends(controller)])
    def set_lines(body: LinesRequest):
        try:
            return service.set_lines(body.dtr, body.rts)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/send", dependencies=[Depends(controller)])
    def send(body: SendRequest):
        return _send_data(service, body)

    @app.get("/api/sessions", dependencies=[Depends(authorize)])
    def sessions():
        return store.sessions()

    @app.get("/api/sessions/{sid}/records", dependencies=[Depends(authorize)])
    def records(sid: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=2000)):
        if not store.exists(sid):
            raise HTTPException(404, "会话不存在")
        return store.read(sid, after, limit)

    @app.get("/api/sessions/{sid}/records/live", dependencies=[Depends(authorize)])
    def records_live(sid: str, after: int = Query(0, ge=0),
                     timeout: float = Query(20, ge=1, le=60)):
        """Long poll: returns as soon as new records exist or the session stops."""
        if not store.exists(sid):
            raise HTTPException(404, "会话不存在")
        owner = manager.by_session(sid)
        deadline = time.monotonic() + timeout

        def payload(page, snapshot):
            return {**page, "state": snapshot["state"], "error": snapshot["error"],
                    "rx_bytes": snapshot["rx_bytes"], "tx_bytes": snapshot["tx_bytes"],
                    "log_file": snapshot["log_file"]}

        if owner is None:
            return payload(store.read(sid, after, 800),
                           {"state": "disconnected", "error": None,
                            "rx_bytes": 0, "tx_bytes": 0, "log_file": None})
        with owner.changed:
            while True:
                page = store.read(sid, after, 800)
                snapshot = owner.status()
                if page["records"] or snapshot["state"] not in ("connected", "connecting"):
                    return payload(page, snapshot)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return payload(page, snapshot)
                owner.changed.wait(min(remaining, 1.0))

    @app.get("/api/sessions/{sid}/export", dependencies=[Depends(authorize)])
    def export(sid: str):
        if not store.exists(sid):
            raise HTTPException(404, "会话不存在")
        # Bound export to the latest record visible at request time.
        with store.lock:
            upper = store.db.execute(
                "SELECT COALESCE(MAX(id),0) FROM records WHERE session_id=?", (sid,)).fetchone()[0]

        def chunks():
            yield json.dumps({"type": "session", "session": next(
                (s for s in store.sessions() if s["id"] == sid), {"id": sid})}, ensure_ascii=False) + "\n"
            cursor = 0
            while cursor < upper:
                page = store.read(sid, cursor)
                if not page["records"]:
                    break
                for record in page["records"]:
                    if record["id"] > upper:
                        return
                    yield json.dumps(record, ensure_ascii=False) + "\n"
                cursor = page["next_cursor"]

        return StreamingResponse(chunks(), media_type="application/x-ndjson",
                                 headers={"Content-Disposition": f'attachment; filename="session-{sid}.jsonl"'})

    static = Path(__file__).parent / "web"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
