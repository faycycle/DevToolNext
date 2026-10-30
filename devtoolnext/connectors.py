"""Byte-stream connections: serial, telnet and ssh share one pyserial-like interface."""
import socket
import threading
import time

from .serial_plugin import SerialPlugin

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
READ_TIMEOUT = 0.1


class ThreadedStream:
    """Expose read/write/in_waiting on top of a blocking socket or channel."""

    def __init__(self):
        self._buffer = bytearray()
        self._lock = threading.Condition()
        self._closed = False
        self._reader = threading.Thread(target=self._loop, name="stream-reader", daemon=True)

    def start(self):
        self._reader.start()
        return self

    def _pull(self, size):
        raise NotImplementedError

    def _send(self, data):
        raise NotImplementedError

    def _shutdown(self):
        pass

    def _loop(self):
        while not self._closed:
            try:
                data = self._pull(4096)
            except Exception:
                data = None
            if data is None:
                break
            if data:
                with self._lock:
                    self._buffer.extend(data)
                    self._lock.notify_all()
        with self._lock:
            self._closed = True
            self._lock.notify_all()

    @property
    def in_waiting(self):
        with self._lock:
            return len(self._buffer)

    def read(self, size=1):
        deadline = time.monotonic() + READ_TIMEOUT
        with self._lock:
            while not self._buffer and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._lock.wait(remaining)
            data = bytes(self._buffer[:size])
            del self._buffer[:size]
        return data

    def write(self, data):
        if self._closed:
            raise OSError("连接已关闭")
        return self._send(bytes(data))

    def close(self):
        self._closed = True
        try:
            self._shutdown()
        except Exception:
            pass
        with self._lock:
            self._lock.notify_all()


class TelnetStream(ThreadedStream):
    def __init__(self, host, port, timeout=8):
        super().__init__()
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(READ_TIMEOUT)
        self._pending = bytearray()

    def _reply(self, command, option):
        try:
            self.sock.sendall(bytes([IAC, command, option]))
        except OSError:
            pass

    def _filter(self, data):
        """Strip IAC negotiation, refusing every option the server offers."""
        self._pending.extend(data)
        buf = self._pending
        out = bytearray()
        i = 0
        while i < len(buf):
            byte = buf[i]
            if byte != IAC:
                out.append(byte)
                i += 1
                continue
            if i + 1 >= len(buf):
                break
            command = buf[i + 1]
            if command == IAC:
                out.append(IAC)
                i += 2
                continue
            if command in (DO, DONT, WILL, WONT):
                if i + 2 >= len(buf):
                    break
                option = buf[i + 2]
                if command == DO:
                    self._reply(WONT, option)
                elif command == WILL:
                    self._reply(DONT, option)
                i += 3
                continue
            if command == SB:
                end = buf.find(bytes([IAC, SE]), i)
                if end < 0:
                    break
                i = end + 2
                continue
            i += 2
        del buf[:i]
        return bytes(out)

    def _pull(self, size):
        try:
            raw = self.sock.recv(size)
        except socket.timeout:
            return b""
        except OSError:
            return None
        if not raw:
            return None
        return self._filter(raw)

    def _send(self, data):
        self.sock.sendall(data)
        return len(data)

    def _shutdown(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class SshStream(ThreadedStream):
    def __init__(self, host, port, username, password="", keyfile="", timeout=10):
        super().__init__()
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("缺少 paramiko，无法使用 SSH") from exc
        if not username:
            raise RuntimeError("SSH 需要用户名")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(hostname=host, port=port, username=username,
                           password=password or None, key_filename=keyfile or None,
                           timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
                           allow_agent=True, look_for_keys=True)
        except paramiko.AuthenticationException as exc:
            client.close()
            raise RuntimeError("SSH 认证失败：用户名、密码或密钥不正确") from exc
        except Exception as exc:
            client.close()
            raise RuntimeError(f"SSH 连接失败：{exc}") from exc
        try:
            self.channel = client.invoke_shell(term="xterm-256color", width=160, height=48)
        except Exception as exc:
            client.close()
            raise RuntimeError(f"SSH 无法打开交互式会话：{exc}") from exc
        self.channel.settimeout(READ_TIMEOUT)
        self.client = client

    def _pull(self, size):
        try:
            data = self.channel.recv(size)
        except socket.timeout:
            return b""
        except Exception:
            return None
        if not data:
            return None if self.channel.exit_status_ready() else b""
        return data

    def _send(self, data):
        self.channel.sendall(data)
        return len(data)

    def _shutdown(self):
        try:
            self.channel.close()
        finally:
            self.client.close()


def tcp_port(config):
    explicit = int(config.get("tcp_port") or 0)
    if explicit:
        return explicit
    return 22 if config.get("type") == "ssh" else 23


def open_stream(config, serial_plugin=None):
    kind = config.get("type") or "serial"
    if kind == "serial":
        return (serial_plugin or SerialPlugin()).open(config)
    host = str(config.get("port") or "").strip()
    if not host:
        raise ValueError("请填写主机地址")
    port = tcp_port(config)
    if kind == "telnet":
        try:
            return TelnetStream(host, port).start()
        except OSError as exc:
            raise RuntimeError(f"Telnet 连接失败：{exc}") from exc
    if kind == "ssh":
        stream = SshStream(host, port, str(config.get("username") or "").strip(),
                           str(config.get("password") or ""),
                           str(config.get("keyfile") or "").strip())
        return stream.start()
    raise ValueError(f"不支持的连接类型：{kind}")
