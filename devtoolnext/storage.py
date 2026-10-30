"""Persistent raw records. SQLite is the authoritative store in the first milestone."""
import base64
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path


class SessionStore:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.process_lock = open(root / "service.lock", "a+b")
        self.process_lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                if self.process_lock.read(1) == b"":
                    self.process_lock.write(b"0")
                    self.process_lock.flush()
                self.process_lock.seek(0)
                msvcrt.locking(self.process_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.process_lock.close()
            raise RuntimeError("此数据目录已由另一个 DevToolNext 进程使用") from exc
        self.lock = threading.Lock()
        self.db = sqlite3.connect(root / "sessions.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, started REAL NOT NULL, ended REAL,
                config TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0, error TEXT);
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                wall REAL NOT NULL, monotonic_ns INTEGER NOT NULL,
                direction TEXT NOT NULL, data BLOB NOT NULL);
            CREATE INDEX IF NOT EXISTS records_session ON records(session_id, id);
            CREATE TABLE IF NOT EXISTS presets (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL,
                created REAL NOT NULL, used REAL NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS presets_config ON presets(config);
        """)
        # A previous process that did not finish leaves an explicit incomplete session.
        self.db.execute("UPDATE sessions SET ended=?, error=COALESCE(error, ?) WHERE ended IS NULL",
                        (time.time(), "上次进程异常退出，末尾数据可能不完整"))
        self.db.commit()

    def begin(self, config):
        sid = uuid.uuid4().hex
        with self.lock:
            self.db.execute("INSERT INTO sessions(id, started, config) VALUES (?, ?, ?)",
                            (sid, time.time(), json.dumps(config)))
            self.db.commit()
        return sid

    def append_batch(self, batch):
        with self.lock:
            with self.db:
                self.db.executemany(
                    "INSERT INTO records(session_id, wall, monotonic_ns, direction, data) VALUES (?, ?, ?, ?, ?)",
                    batch)

    def finish(self, sid, error=None):
        with self.lock:
            with self.db:
                self.db.execute("UPDATE sessions SET ended=?, complete=?, error=? WHERE id=?",
                                (time.time(), int(error is None), error, sid))

    def presets(self, limit=20):
        with self.lock:
            rows = self.db.execute(
                "SELECT id, name, config, created, used FROM presets ORDER BY used DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(id=r[0], name=r[1], config=json.loads(r[2]), created=r[3], used=r[4])
                for r in rows]

    @staticmethod
    def preset_key(config):
        return json.dumps(config, sort_keys=True, ensure_ascii=False)

    def save_preset(self, config, name=None):
        key = self.preset_key(config)
        label = (name or "").strip() or "{} · {}".format(
            config.get("port", "未知端口"), config.get("baudrate", ""))
        now = time.time()
        with self.lock, self.db:
            row = self.db.execute("SELECT id, name FROM presets WHERE config=?", (key,)).fetchone()
            if row:
                if name:
                    self.db.execute("UPDATE presets SET name=?, used=? WHERE id=?", (label[:60], now, row[0]))
                else:
                    self.db.execute("UPDATE presets SET used=? WHERE id=?", (now, row[0]))
                return row[0]
            pid = uuid.uuid4().hex
            self.db.execute("INSERT INTO presets(id, name, config, created, used) VALUES (?,?,?,?,?)",
                            (pid, label[:60], key, now, now))
        return pid

    def delete_preset(self, pid):
        with self.lock, self.db:
            return self.db.execute("DELETE FROM presets WHERE id=?", (pid,)).rowcount > 0

    def sessions(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT id, started, ended, config, complete, error FROM sessions ORDER BY started DESC LIMIT 100").fetchall()
        return [dict(id=r[0], started=r[1], ended=r[2], config=json.loads(r[3]),
                     complete=bool(r[4]), error=r[5]) for r in rows]

    def exists(self, sid):
        with self.lock:
            return self.db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone() is not None

    def read(self, sid, after=0, limit=200):
        with self.lock:
            rows = self.db.execute(
                "SELECT id, wall, monotonic_ns, direction, data FROM records "
                "WHERE session_id=? AND id>? ORDER BY id LIMIT ?", (sid, after, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        return {"records": [dict(id=r[0], wall=r[1], monotonic_ns=r[2], direction=r[3],
                                 data_base64=base64.b64encode(r[4]).decode()) for r in rows],
                "next_cursor": rows[-1][0] if rows else after, "has_more": more}

    def close(self):
        with self.lock:
            self.db.close()
            self.process_lock.close()
