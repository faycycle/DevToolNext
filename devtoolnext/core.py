"""Single connection owner, bounded acquisition queue, independent persistent writer."""
import logging
import queue
import threading
import time
import uuid

from . import __version__
from .connectors import open_stream
from .serial_plugin import SerialPlugin
from .sessionlog import SessionLog, describe_target
from .storage import SessionStore, public_config

log = logging.getLogger(__name__)

READ_CHUNK = 4096      # 单次驱动读取上限
MERGE_LIMIT = 16384    # 一次合并入队的原始字节上限
QUEUE_SIZE = 1024
FLUSH_INTERVAL = 0.025   # 批次落库/推送间隔，决定画面更新频率


class DeviceService:
    def __init__(self, store: SessionStore, plugin=None):
        self.store = store
        self.plugin = plugin or SerialPlugin()
        self.lifecycle = threading.RLock()
        self.state_lock = threading.Lock()
        self.cid = uuid.uuid4().hex
        self.io = None
        self.sid = None
        self.error = None
        self.config = None
        self.state = "disconnected"
        self.rx = self.tx = 0
        self.reader = self.writer = None
        self.session_log = None
        self.last_log_file = None
        self.changed = threading.Condition()
        self.stop = threading.Event()
        self.reader_done = threading.Event()
        self.pending = queue.Queue(maxsize=QUEUE_SIZE)

    def status(self):
        lines = self._lines()
        with self.state_lock:
            return dict(state=self.state, session_id=self.sid, error=self.error,
                        config=self.config, rx_bytes=self.rx, tx_bytes=self.tx,
                        log_file=str(self.session_log.path) if self.session_log else self.last_log_file,
                        dtr=lines.get("dtr"), rts=lines.get("rts"),
                        capabilities=list(self.plugin.capabilities))

    def _lines(self):
        io = self.io
        if io is None:
            return {}
        try:
            return {"dtr": bool(io.dtr), "rts": bool(io.rts)}
        except Exception:
            return {}

    def set_lines(self, dtr=None, rts=None):
        """Drive DTR/RTS manually; many boards reset on a DTR toggle."""
        with self.lifecycle:
            if self.state != "connected" or self.io is None:
                raise RuntimeError("设备未连接")
            if dtr is None and rts is None:
                raise ValueError("请指定 DTR 或 RTS")
            if not hasattr(self.io, "dtr"):
                raise ValueError("该连接类型不支持 DTR/RTS")
            try:
                if dtr is not None:
                    self.io.dtr = bool(dtr)
                if rts is not None:
                    self.io.rts = bool(rts)
            except Exception as exc:
                self._fail(f"设置 DTR/RTS 失败：{exc}")
                raise
            lines = self._lines()
            log.info("线路状态 session=%s %s", self.sid, lines)
            self.notify()
            return lines

    def connect(self, config):
        config = dict(config or {})
        public = public_config(config)
        with self.lifecycle:
            self.disconnect()
            with self.state_lock:
                self.state, self.error = "connecting", None
            try:
                port = open_stream(config, self.plugin)
            except Exception as exc:
                log.warning("连接失败 port=%s: %s", config.get("port"), exc)
                self._fail(str(exc))
                raise
            sid = uuid.uuid4().hex
            try:
                session_log = SessionLog(self.store.output_dir(), sid, public, __version__,
                                         keep_raw=self.store.get_setting("keep_raw", "1") != "0")
                self.store.begin(public, sid)
            except Exception as exc:
                try:
                    port.close()
                except Exception:
                    log.warning("关闭端口失败", exc_info=True)
                log.warning("会话初始化失败 port=%s: %s", config.get("port"), exc)
                self._fail(str(exc))
                raise
            self.io, self.sid, self.session_log = port, sid, session_log
            self.config = public
            self.rx = self.tx = 0
            self.pending = queue.Queue(maxsize=QUEUE_SIZE)
            self.stop.clear()
            self.reader_done.clear()
            with self.state_lock:
                self.state = "connected"
            self.writer = threading.Thread(target=self._write_records, name="record-writer", daemon=True)
            self.reader = threading.Thread(target=self._read, name="serial-reader", daemon=True)
            self.writer.start()
            self.reader.start()
            log.info("已连接 port=%s session=%s log=%s", config.get("port"), sid, session_log.path)
            self.notify()
            return self.status()

    def _fail(self, message):
        with self.state_lock:
            if self.error == message and self.state == "fault":
                return
            self.error = message
            self.state = "fault"
        log.error("%s", message)
        self.stop.set()
        self.notify()

    def _record(self, direction, data):
        try:
            self.pending.put_nowait((self.sid, time.time(), time.monotonic_ns(), direction, bytes(data)))
        except queue.Full:
            self._fail("录制队列已满，会话不完整；已停止采集")
            raise RuntimeError(self.error)

    def _read(self):
        io = self.io
        try:
            while not self.stop.is_set():
                data = io.read(min(max(io.in_waiting, 1), READ_CHUNK))
                if not data:
                    continue
                # Coalesce whatever the driver already buffered into one record.
                buffer = bytearray(data)
                while len(buffer) < MERGE_LIMIT:
                    waiting = io.in_waiting
                    if not waiting:
                        break
                    more = io.read(min(waiting, MERGE_LIMIT - len(buffer)))
                    if not more:
                        break
                    buffer.extend(more)
                chunk = bytes(buffer)
                self._record("rx", chunk)
                with self.state_lock:
                    self.rx += len(chunk)
        except Exception as exc:
            with self.state_lock:
                already_failed = self.state == "fault"
            if not already_failed:
                self._fail(f"采集停止：{exc}")
        finally:
            self.reader_done.set()

    def _write_records(self):
        try:
            while not self.reader_done.is_set() or not self.pending.empty():
                try:
                    batch = [self.pending.get(timeout=FLUSH_INTERVAL)]
                except queue.Empty:
                    continue
                while len(batch) < 128:
                    try:
                        batch.append(self.pending.get_nowait())
                    except queue.Empty:
                        break
                self._persist(batch)
        except Exception as exc:
            self._fail(f"录制写入失败，会话不完整：{exc}")

    def _persist(self, batch):
        # The JSONL file is the durable artifact; the database only backs the live view.
        if self.session_log is not None:
            try:
                self.session_log.append(batch)
            except Exception as exc:
                raise RuntimeError(f"日志文件写入失败：{exc}") from exc
        self.store.append_batch(batch)
        self.notify()

    def notify(self):
        """Wake long-poll readers as soon as a batch is queryable."""
        with self.changed:
            self.changed.notify_all()

    def send(self, data):
        if not data:
            raise ValueError("发送内容不能为空")
        with self.lifecycle:
            if self.state != "connected" or self.stop.is_set():
                raise RuntimeError("设备未连接")
            try:
                count = self.io.write(data)
                if count:
                    self._record("tx", data[:count])
                    with self.state_lock:
                        self.tx += count
                if count != len(data):
                    raise RuntimeError(f"部分写入：{count}/{len(data)} 字节；不要自动重试")
                return {"written": count, "device_execution": "unknown"}
            except Exception as exc:
                self._fail(f"发送失败，设备可能已收到部分数据：{exc}")
                raise

    def _close_io(self):
        port, self.io = self.io, None
        if port is None:
            return
        try:
            port.close()
        except Exception:
            log.warning("关闭端口失败", exc_info=True)

    def disconnect(self):
        with self.lifecycle:
            self.stop.set()
            stuck = []
            if self.reader:
                self.reader.join(timeout=3)
                if self.reader.is_alive():
                    # A driver ignoring the read timeout must not block disconnecting forever.
                    self._close_io()
                    self.reader.join(timeout=2)
                    if self.reader.is_alive():
                        stuck.append("采集线程")
            if self.writer:
                self.writer.join(timeout=5)
                if self.writer.is_alive():
                    stuck.append("录制线程")
            self._close_io()
            if self.session_log is not None:
                self.last_log_file = str(self.session_log.path)
                try:
                    self.session_log.close(self.error is None, self.error)
                except Exception:
                    log.exception("关闭会话日志失败 session=%s", self.sid)
                self.session_log = None
            if self.sid:
                try:
                    self.store.finish(self.sid, self.error)
                except Exception:
                    log.exception("标记会话结束失败 session=%s", self.sid)
            self.reader = self.writer = None
            with self.state_lock:
                self.state = "disconnected"
                self.error = None
            log.info("已断开 session=%s 未退出线程=%s", self.sid, stuck or "无")
            self.notify()
            if stuck:
                raise RuntimeError("、".join(stuck) + "仍在退出，不能开启新连接")
            return self.status()

    def close(self):
        try:
            self.disconnect()
        finally:
            self.store.close()


class ConnectionManager:
    """Several simultaneous connections share one store; `default` keeps the legacy single-connection API."""

    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()
        self._items = {}
        self.default = DeviceService(store)

    def add(self, service):
        with self._lock:
            self._items[service.cid] = service

    def get(self, cid):
        with self._lock:
            return self._items.get(cid)

    def remove(self, cid):
        with self._lock:
            return self._items.pop(cid, None)

    def items(self):
        with self._lock:
            return list(self._items.values())

    def by_session(self, sid):
        if not sid:
            return None
        for service in self.items():
            if service.sid == sid:
                return service
        return self.default if self.default.sid == sid else None

    def list(self):
        return [self.describe(service) for service in self.items()]

    @staticmethod
    def describe(service):
        state = service.status()
        return {"id": service.cid, "label": describe_target(state["config"] or {}), **state}

    def close_all(self):
        for service in self.items():
            try:
                service.disconnect()
            except Exception:
                log.warning("关闭连接失败 cid=%s", service.cid, exc_info=True)
        with self._lock:
            self._items.clear()
        try:
            self.default.disconnect()
        except Exception:
            log.warning("关闭默认连接失败", exc_info=True)
