"""MCP (Model Context Protocol) over JSON-RPC: let agents drive serial/telnet/ssh."""
import base64
import json
import re
import time

from . import __version__
from .core import ConnectionManager, DeviceService
from .sessionlog import describe_target

PROTOCOL_VERSION = "2025-06-18"
MAX_TEXT = 4000


class ToolError(Exception):
    pass


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


TOOLS = [
    {
        "name": "list_connections",
        "description": "列出当前已打开的连接（串口 / Telnet / SSH）及其状态。",
        "inputSchema": _schema({}),
    },
    {
        "name": "open_connection",
        "description": "新建一个连接。type=serial 时 port 是设备端口（如 COM3 或 loop://）；"
                       "type=telnet/ssh 时 port 是主机地址，tcp_port 是网络端口。",
        "inputSchema": _schema({
            "type": {"type": "string", "enum": ["serial", "telnet", "ssh"], "default": "serial"},
            "port": {"type": "string", "description": "设备端口或主机地址"},
            "baudrate": {"type": "integer", "default": 115200},
            "bytesize": {"type": "integer", "enum": [5, 6, 7, 8], "default": 8},
            "parity": {"type": "string", "enum": ["N", "E", "O", "M", "S"], "default": "N"},
            "stopbits": {"type": "number", "enum": [1, 1.5, 2], "default": 1},
            "rtscts": {"type": "boolean", "default": False},
            "tcp_port": {"type": "integer", "description": "Telnet 默认 23，SSH 默认 22"},
            "username": {"type": "string", "description": "SSH 用户名"},
            "password": {"type": "string", "description": "SSH 密码（不会写入磁盘）"},
            "keyfile": {"type": "string", "description": "SSH 私钥路径"},
        }, ["port"]),
    },
    {
        "name": "close_connection",
        "description": "关闭指定连接。",
        "inputSchema": _schema({"connection_id": {"type": "string"}}, ["connection_id"]),
    },
    {
        "name": "send_command",
        "description": "向连接写入数据。默认按文本发送并在末尾追加 CR；返回发送前的游标，"
                       "可直接传给 wait_for_output 只等这次命令的回复。",
        "inputSchema": _schema({
            "connection_id": {"type": "string"},
            "data": {"type": "string", "description": "要发送的文本，或 format=hex 时的十六进制字节"},
            "format": {"type": "string", "enum": ["text", "hex"], "default": "text"},
            "encoding": {"type": "string", "enum": ["utf-8", "gb18030", "ascii"], "default": "utf-8"},
            "ending": {"type": "string", "enum": ["none", "cr", "lf", "crlf"], "default": "cr"},
        }, ["connection_id", "data"]),
    },
    {
        "name": "read_output",
        "description": "读取连接在某游标之后收到的数据（默认只返回设备输出）。",
        "inputSchema": _schema({
            "connection_id": {"type": "string"},
            "after": {"type": "integer", "default": 0, "description": "上次返回的 next_cursor"},
            "limit": {"type": "integer", "default": 200, "minimum": 1, "maximum": 500},
            "include_tx": {"type": "boolean", "default": False, "description": "是否包含本机发送的记录"},
        }, ["connection_id"]),
    },
    {
        "name": "wait_for_output",
        "description": "阻塞等待设备输出中出现指定内容，常用于发送命令后等提示符或 OK。",
        "inputSchema": _schema({
            "connection_id": {"type": "string"},
            "pattern": {"type": "string", "description": "要等待的文本或正则"},
            "after": {"type": "integer", "default": 0},
            "timeout": {"type": "number", "default": 10, "minimum": 0.5, "maximum": 120},
            "regex": {"type": "boolean", "default": False},
        }, ["connection_id", "pattern"]),
    },
    {
        "name": "connection_info",
        "description": "查看连接详情：状态、收发字节数、当前日志文件路径。",
        "inputSchema": _schema({"connection_id": {"type": "string"}}, ["connection_id"]),
    },
    {
        "name": "list_serial_ports",
        "description": "列出本机可用的串口。",
        "inputSchema": _schema({}),
    },
]

_ENDINGS = {"none": "", "cr": "\r", "lf": "\n", "crlf": "\r\n"}


class McpHandler:
    def __init__(self, manager: ConnectionManager, store, version: str = ""):
        self.manager = manager
        self.store = store
        self.version = version

    # ---- JSON-RPC ----
    def handle(self, message, role="control"):
        if not isinstance(message, dict):
            return _error(None, -32600, "无效的 JSON-RPC 请求")
        method = message.get("method")
        msg_id = message.get("id")
        if msg_id is None and method and method.startswith("notifications/"):
            return None
        try:
            if method == "initialize":
                return _result(msg_id, {
                    "protocolVersion": (message.get("params") or {}).get("protocolVersion", PROTOCOL_VERSION),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "devtoolnext", "version": self.version or __version__},
                })
            if method == "ping":
                return _result(msg_id, {})
            if method == "tools/list":
                return _result(msg_id, {"tools": TOOLS})
            if method == "tools/call":
                params = message.get("params") or {}
                return self._call_tool(msg_id, params.get("name"), params.get("arguments") or {}, role)
            return _error(msg_id, -32601, f"不支持的方法：{method}")
        except Exception as exc:  # pragma: no cover - defensive
            return _error(msg_id, -32603, str(exc))

    def _call_tool(self, msg_id, name, args, role):
        handler = getattr(self, f"_tool_{name}", None) if name else None
        if handler is None:
            return _error(msg_id, -32602, f"未知工具：{name}")
        try:
            payload = handler(args, role)
        except ToolError as exc:
            return _result(msg_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except PermissionError as exc:
            return _result(msg_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:
            return _result(msg_id, {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                                    "isError": True})
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
        return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})

    # ---- helpers ----
    @staticmethod
    def _need_control(role):
        if role != "control":
            raise PermissionError("当前密钥为只读，不能执行该操作")

    def _service(self, args):
        cid = str(args.get("connection_id") or "")
        service = self.manager.get(cid)
        if service is None:
            raise ToolError(f"连接不存在：{cid}，请先调用 list_connections")
        return service

    @staticmethod
    def _summary(service: DeviceService):
        state = service.status()
        config = state.get("config") or {}
        return {
            "id": service.cid,
            "type": config.get("type", "serial"),
            "target": describe_target(config) if config else "",
            "state": state.get("state"),
            "session_id": state.get("session_id"),
            "rx_bytes": state.get("rx_bytes"),
            "tx_bytes": state.get("tx_bytes"),
            "log_file": state.get("log_file"),
            "error": state.get("error"),
        }

    def _record(self, row):
        data = base64.b64decode(row["data_base64"])
        return {
            "id": row["id"],
            "time": time.strftime("%H:%M:%S", time.localtime(row["wall"])) + f".{int(row['wall']*1000)%1000:03d}",
            "direction": row["direction"],
            "text": data.decode("utf-8", "replace"),
            "hex": data.hex(" "),
            "bytes": len(data),
        }

    # ---- tools ----
    def _tool_list_connections(self, args, role):
        return [self._summary(service) for service in self.manager.items()]

    def _tool_open_connection(self, args, role):
        self._need_control(role)
        kind = str(args.get("type") or "serial")
        if kind not in ("serial", "telnet", "ssh"):
            raise ToolError("type 只能是 serial、telnet 或 ssh")
        port = str(args.get("port") or "").strip()
        if not port:
            raise ToolError("请提供 port（设备端口或主机地址）")
        stopbits = float(args.get("stopbits") or 1)
        if stopbits.is_integer():
            stopbits = int(stopbits)
        config = {
            "type": kind,
            "port": port,
            "baudrate": int(args.get("baudrate") or 115200),
            "bytesize": int(args.get("bytesize") or 8),
            "parity": str(args.get("parity") or "N"),
            "stopbits": stopbits,
            "rtscts": bool(args.get("rtscts", False)),
            "tcp_port": int(args.get("tcp_port") or 0),
            "username": str(args.get("username") or ""),
            "password": str(args.get("password") or ""),
            "keyfile": str(args.get("keyfile") or ""),
        }
        service = DeviceService(self.store)
        try:
            service.connect(config)
        except Exception as exc:
            raise ToolError(f"连接失败：{exc}") from exc
        self.manager.add(service)
        try:
            self.store.save_preset(config)
        except Exception:
            pass
        return self._summary(service)

    def _tool_close_connection(self, args, role):
        self._need_control(role)
        service = self._service(args)
        try:
            service.disconnect()
        except Exception as exc:
            raise ToolError(f"断开失败：{exc}") from exc
        self.manager.remove(service.cid)
        return {"closed": service.cid}

    def _tool_send_command(self, args, role):
        self._need_control(role)
        service = self._service(args)
        data = str(args.get("data") or "")
        if not data:
            raise ToolError("data 不能为空")
        if str(args.get("format") or "text") == "hex":
            try:
                payload = bytes.fromhex(data)
            except ValueError as exc:
                raise ToolError(f"HEX 内容无效：{exc}") from exc
        else:
            encoding = str(args.get("encoding") or "utf-8")
            payload = (data + _ENDINGS.get(str(args.get("ending") or "cr"), "\r")).encode(encoding)
        cursor = self.store.max_id(service.sid) if service.sid else 0
        try:
            written = service.send(payload)["written"]
        except Exception as exc:
            raise ToolError(f"发送失败：{exc}") from exc
        return {"written": written, "cursor": cursor, "target": describe_target(service.status().get("config") or {})}

    def _tool_read_output(self, args, role):
        service = self._service(args)
        if not service.sid:
            raise ToolError("该连接还没有会话")
        limit = max(1, min(500, int(args.get("limit") or 200)))
        include_tx = bool(args.get("include_tx", False))
        page = self.store.read(service.sid, int(args.get("after") or 0), limit)
        rows = [self._record(row) for row in page["records"]
                if include_tx or row["direction"] == "rx"]
        return {"records": rows, "next_cursor": page["next_cursor"], "has_more": page["has_more"],
                "state": service.status().get("state")}

    def _tool_wait_for_output(self, args, role):
        service = self._service(args)
        if not service.sid:
            raise ToolError("该连接还没有会话")
        pattern = str(args.get("pattern") or "")
        if not pattern:
            raise ToolError("pattern 不能为空")
        timeout = max(0.5, min(120.0, float(args.get("timeout") or 10)))
        regex = bool(args.get("regex", False))
        compiled = None
        if regex:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise ToolError(f"正则无效：{exc}") from exc
        cursor = int(args.get("after") or 0)
        deadline = time.monotonic() + timeout
        chunks, size = [], 0
        while True:
            page = self.store.read(service.sid, cursor, 800)
            for row in page["records"]:
                if row["direction"] != "rx":
                    continue
                text = base64.b64decode(row["data_base64"]).decode("utf-8", "replace")
                chunks.append(text)
                size += len(text)
            if page["records"]:
                cursor = page["next_cursor"]
            text = "".join(chunks)
            if size > 200_000:
                text = text[-200_000:]
                chunks, size = [text], len(text)
            matched = bool(compiled.search(text)) if compiled else (pattern in text)
            if matched or time.monotonic() >= deadline:
                return {"matched": matched, "cursor": cursor,
                        "text": text[-MAX_TEXT:], "state": service.status().get("state")}
            with service.changed:
                service.changed.wait(min(0.25, max(0.0, deadline - time.monotonic())))

    def _tool_connection_info(self, args, role):
        return self._summary(self._service(args))

    def _tool_list_serial_ports(self, args, role):
        return self.manager.default.plugin.discover()


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
