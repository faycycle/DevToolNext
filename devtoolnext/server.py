import base64
import binascii
import hmac
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core import DeviceService
from . import __version__
from .storage import SessionStore


class ConnectRequest(BaseModel):
    port: str = Field(min_length=1, max_length=256)
    baudrate: int = Field(default=115200, ge=50, le=4000000)
    bytesize: Literal[5, 6, 7, 8] = 8
    parity: Literal["N", "E", "O", "M", "S"] = "N"
    stopbits: Literal[1, 1.5, 2] = 1
    rtscts: bool = False


class SendRequest(BaseModel):
    data: str = Field(min_length=1, max_length=16384)
    format: Literal["text", "hex"] = "text"
    encoding: Literal["utf-8", "gb18030", "ascii"] = "utf-8"
    ending: Literal["", "\n", "\r", "\r\n"] = "\n"


class PresetRequest(BaseModel):
    name: str = Field(default="", max_length=60)
    config: ConnectRequest


def create_app(data_dir: Path, control_token=None, view_token=None, service=None):
    control_token = control_token or secrets.token_urlsafe(32)
    view_token = view_token or secrets.token_urlsafe(32)
    service = service or DeviceService(SessionStore(data_dir))

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.close()

    app = FastAPI(title="DevToolNext", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.service = service
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

    def authorize(request: Request, authorization: str = Header(default="")):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "不允许跨来源请求")
        supplied = authorization.removeprefix("Bearer ")
        if supplied and hmac.compare_digest(supplied.encode(), control_token.encode()):
            return "control"
        if supplied and hmac.compare_digest(supplied.encode(), view_token.encode()):
            return "view"
        raise HTTPException(401, "访问密钥无效")

    def controller(role=Depends(authorize)):
        if role != "control":
            raise HTTPException(403, "当前为只读权限")

    @app.get("/api/status")
    def status(role=Depends(authorize)):
        return {**service.status(), "role": role, "version": __version__,
                "presets": service.store.presets()}

    @app.get("/api/ports", dependencies=[Depends(authorize)])
    def ports():
        return service.plugin.discover()

    @app.get("/api/presets", dependencies=[Depends(authorize)])
    def presets():
        return service.store.presets()

    @app.post("/api/presets", dependencies=[Depends(controller)])
    def save_preset(body: PresetRequest):
        pid = service.store.save_preset(body.config.model_dump(), body.name)
        return next((p for p in service.store.presets(50) if p["id"] == pid), {"id": pid})

    @app.delete("/api/presets/{pid}", dependencies=[Depends(controller)])
    def delete_preset(pid: str):
        if not service.store.delete_preset(pid):
            raise HTTPException(404, "预设不存在")
        return {"deleted": pid}

    @app.post("/api/connect", dependencies=[Depends(controller)])
    def connect(body: ConnectRequest):
        config = body.model_dump()
        try:
            connected = service.connect(config)
        except Exception as exc:
            raise HTTPException(409, str(exc)) from exc
        # Keep the parameters that actually worked as a reusable preset.
        try:
            service.store.save_preset(config)
        except Exception:
            logging.getLogger(__name__).exception("保存连接预设失败")
        return connected

    @app.post("/api/disconnect", dependencies=[Depends(controller)])
    def disconnect():
        try:
            return service.disconnect()
        except Exception as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/send", dependencies=[Depends(controller)])
    def send(body: SendRequest):
        try:
            if body.format == "hex":
                try:
                    data = bytes.fromhex(body.data)
                except (ValueError, binascii.Error) as exc:
                    raise HTTPException(
                        400, f"HEX 内容无效：{exc}。只允许 0-9 / A-F 与空格") from exc
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

    @app.get("/api/sessions", dependencies=[Depends(authorize)])
    def sessions():
        return service.store.sessions()

    @app.get("/api/sessions/{sid}/records", dependencies=[Depends(authorize)])
    def records(sid: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=2000)):
        if not service.store.exists(sid):
            raise HTTPException(404, "会话不存在")
        return service.store.read(sid, after, limit)

    @app.get("/api/sessions/{sid}/export", dependencies=[Depends(authorize)])
    def export(sid: str):
        if not service.store.exists(sid):
            raise HTTPException(404, "会话不存在")
        # Bound export to the latest record visible at request time.
        import json
        with service.store.lock:
            upper = service.store.db.execute(
                "SELECT COALESCE(MAX(id),0) FROM records WHERE session_id=?", (sid,)).fetchone()[0]

        def chunks():
            yield json.dumps({"type": "session", "session": next(
                (s for s in service.store.sessions() if s["id"] == sid), {"id": sid})}, ensure_ascii=False) + "\n"
            cursor = 0
            while cursor < upper:
                page = service.store.read(sid, cursor)
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
