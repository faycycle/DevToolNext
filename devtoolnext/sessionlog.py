"""Session artifacts: a readable .log for humans plus an optional raw JSONL."""
import base64
import codecs
import json
import re
import time
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(text):
    return _CTRL.sub("", _ANSI.sub("", text))


def describe_target(config):
    kind = config.get("type", "serial")
    where = config.get("port") or config.get("host") or "?"
    if kind == "serial":
        stop = config.get("stopbits", "")
        try:
            stop = "{:g}".format(float(stop))
        except (TypeError, ValueError):
            pass
        return "{} @{} {}{}{}".format(where, config.get("baudrate", ""),
                                     config.get("bytesize", ""), config.get("parity", ""), stop)
    return "{}://{}:{}".format(kind, where, config.get("tcp_port") or "")


class SessionLog:
    """One .log file per connection;每行一条设备输出，带时间戳与方向。"""

    def __init__(self, directory: Path, sid: str, config: dict, version: str = "", keep_raw: bool = True):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        host = _UNSAFE.sub("_", str(config.get("port") or "target"))[:40] or "target"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = f"{stamp}_{host}_{sid[:8]}"
        self.path = directory / f"{base}.log"
        self.raw_path = directory / f"{base}.jsonl" if keep_raw else None
        self.records = 0
        self._last_wall = time.time()
        self._decoders = {"rx": codecs.getincrementaldecoder("utf-8")("replace"),
                          "tx": codecs.getincrementaldecoder("utf-8")("replace")}
        self._pending = {"rx": "", "tx": ""}
        self.file = open(self.path, "w", encoding="utf-8", newline="\n")
        self.raw = open(self.raw_path, "w", encoding="utf-8", newline="\n") if self.raw_path else None
        self.file.write("# DevToolNext {} session log\n".format(version or ""))
        self.file.write("# session: {}\n".format(sid))
        self.file.write("# target: {}\n".format(describe_target(config)))
        self.file.write("# started: {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S")))
        if self.raw is not None:
            self.raw.write(json.dumps({"type": "session", "session_id": sid, "started": time.time(),
                                       "version": version, "config": config},
                                      ensure_ascii=False) + "\n")

    def _stamp(self, wall):
        return "{}.{:03d}".format(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(wall)),
                                  int(wall * 1000) % 1000)

    def _write_line(self, wall, direction, text):
        self.file.write("{} {}  {}\n".format(self._stamp(wall), direction.upper(), _clean(text)))

    def append(self, batch):
        for _sid, wall, monotonic_ns, direction, data in batch:
            self.records += 1
            self._last_wall = wall
            if self.raw is not None:
                self.raw.write(json.dumps({"seq": self.records, "wall": wall,
                                           "monotonic_ns": monotonic_ns, "direction": direction,
                                           "data_base64": base64.b64encode(data).decode("ascii")},
                                          ensure_ascii=False) + "\n")
            decoder = self._decoders.get(direction)
            if decoder is None:
                continue
            text = decoder.decode(data)
            if not text:
                continue
            buffered = self._pending[direction] + text
            lines = buffered.split("\n")
            self._pending[direction] = lines.pop()
            for line in lines:
                self._write_line(wall, direction, line.rstrip("\r"))
        self.file.flush()
        if self.raw is not None:
            self.raw.flush()

    def close(self, complete, error=None):
        try:
            for direction, rest in self._pending.items():
                if rest:
                    self._write_line(self._last_wall, direction, rest.rstrip("\r"))
            self._pending = {"rx": "", "tx": ""}
            self.file.write("# ended: {} complete={} records={}{}\n".format(
                time.strftime("%Y-%m-%d %H:%M:%S"), "true" if complete else "false",
                self.records, "" if not error else " error={}".format(error)))
            self.file.flush()
            if self.raw is not None:
                self.raw.write(json.dumps({"type": "end", "ended": time.time(),
                                           "complete": bool(complete), "records": self.records,
                                           "error": error}, ensure_ascii=False) + "\n")
                self.raw.flush()
        finally:
            self.file.close()
            if self.raw is not None:
                self.raw.close()
