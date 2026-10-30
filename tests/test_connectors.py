import socket
import threading
import time

import pytest

from devtoolnext.connectors import SshStream, TelnetStream


def _read_until(stream, needle, timeout=5):
    buffer = b""
    deadline = time.monotonic() + timeout
    while needle not in buffer and time.monotonic() < deadline:
        buffer += stream.read(256)
    return buffer


def _telnet_echo_server():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def run():
        try:
            conn, _ = server.accept()
        except OSError:
            return
        try:
            conn.sendall(bytes([255, 253, 1]))  # IAC DO ECHO, must be stripped
            while True:
                data = conn.recv(1024)
                if not data:
                    return
                conn.sendall(data)
        except OSError:
            return
        finally:
            conn.close()

    threading.Thread(target=run, daemon=True).start()
    return server, port


def test_telnet_stream_strips_negotiation_and_echoes():
    server, port = _telnet_echo_server()
    stream = TelnetStream("127.0.0.1", port).start()
    try:
        stream.write(b"ping")
        received = _read_until(stream, b"ping")
        assert b"ping" in received
        assert b"\xff" not in received
    finally:
        stream.close()
        server.close()


class _EchoServer:
    """Minimal SSH server: password auth, PTY shell, echoes back with a prefix."""

    def __init__(self):
        paramiko = pytest.importorskip("paramiko")
        self.paramiko = paramiko
        self.key = paramiko.RSAKey.generate(2048)
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.error = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        paramiko = self.paramiko
        outer = self

        class Handler(paramiko.ServerInterface):
            def check_auth_password(self, username, password):
                if (username, password) == ("u", "p"):
                    return paramiko.AUTH_SUCCESSFUL
                return paramiko.AUTH_FAILED

            def check_channel_request(self, kind, chanid):
                if kind == "session":
                    return paramiko.OPEN_SUCCEEDED
                return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

            def check_channel_shell_request(self, channel):
                return True

            def check_channel_pty_request(self, channel, term, width, height, pixw, pixh, modes):
                return True

            def get_allowed_auths(self, username):
                return "password"

        try:
            client, _ = self.server.accept()
            transport = paramiko.Transport(client)
            transport.add_server_key(self.key)
            transport.start_server(server=Handler())
            channel = transport.accept(10)
            while True:
                data = channel.recv(1024)
                if not data:
                    return
                channel.sendall(b"[ssh] " + data)
        except Exception as exc:  # pragma: no cover - diagnostic only
            outer.error = exc

    def close(self):
        self.server.close()


def test_ssh_stream_roundtrip():
    server = _EchoServer()
    stream = SshStream("127.0.0.1", server.port, "u", "p", timeout=10).start()
    try:
        stream.write(b"hello\n")
        received = _read_until(stream, b"[ssh] hello")
        assert b"[ssh] hello" in received
    finally:
        stream.close()
        server.close()
    assert server.error is None, server.error
