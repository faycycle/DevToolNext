import base64
import json
import time
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from devtoolnext.server import create_app
from devtoolnext.storage import SessionStore


CONTROL = {"Authorization": "Bearer controller"}
VIEW = {"Authorization": "Bearer observer"}


def wait_records(client, sid, expected):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        page = client.get(f"/api/sessions/{sid}/records", headers=VIEW).json()
        received = b"".join(base64.b64decode(r["data_base64"]) for r in page["records"] if r["direction"] == "rx")
        if received == expected:
            return page
        time.sleep(0.02)
    raise AssertionError(f"Loopback data differs: {received!r}")


def test_loopback_records_survive_disconnect_and_export(tmp_path):
    app = create_app(tmp_path, "controller", "observer")
    with TestClient(app) as client:
        connection = client.post("/api/connect", headers=CONTROL, json={"port": "loop://"})
        assert connection.status_code == 200
        sid = connection.json()["session_id"]
        payload = bytes(range(256)) * 4
        result = client.post("/api/send", headers=CONTROL,
                             json={"data": payload.hex(), "format": "hex"})
        assert result.json() == {"written": len(payload), "device_execution": "unknown"}
        page = wait_records(client, sid, payload)
        assert b"".join(base64.b64decode(r["data_base64"]) for r in page["records"] if r["direction"] == "tx") == payload
        assert client.post("/api/disconnect", headers=CONTROL, json={}).status_code == 200
        first = client.get(f"/api/sessions/{sid}/records?limit=1", headers=VIEW).json()
        second = client.get(f"/api/sessions/{sid}/records?after={first['next_cursor']}", headers=VIEW).json()
        assert first["has_more"]
        assert not {r["id"] for r in first["records"]} & {r["id"] for r in second["records"]}
        exported = client.get(f"/api/sessions/{sid}/export", headers=VIEW)
        lines = [json.loads(line) for line in exported.text.splitlines()]
        assert lines[0]["session"]["complete"] is True
        assert len(lines) == len(page["records"]) + 1
    store = SessionStore(tmp_path)
    assert store.read(sid)["records"] == page["records"]
    store.close()


def test_authorization_read_only_and_origin(tmp_path):
    with TestClient(create_app(tmp_path, "controller", "observer")) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/status", headers=VIEW).json()["role"] == "view"
        for endpoint, body in [("connect", {"port": "loop://"}), ("disconnect", {}), ("send", {"data": "x"})]:
            assert client.post("/api/" + endpoint, headers=VIEW, json=body).status_code == 403
        assert client.get("/api/status", headers={**CONTROL, "Origin": "https://untrusted.example"}).status_code == 403
        assert client.get("/api/status", headers={**CONTROL, "Origin": "http://testserver"}).status_code == 200


def test_validation_and_reconnect(tmp_path):
    with TestClient(create_app(tmp_path, "controller", "observer")) as client:
        assert client.post("/api/connect", headers=CONTROL, json={"port": "socket://example.com:80"}).status_code == 409
        assert client.post("/api/connect", headers=CONTROL, json={"port": "loop://", "baudrate": 0}).status_code == 422
        first = client.post("/api/connect", headers=CONTROL, json={"port": "loop://"}).json()
        assert client.post("/api/send", headers=CONTROL, json={"data": "ZZ", "format": "hex"}).status_code == 400
        client.post("/api/disconnect", headers=CONTROL, json={})
        second = client.post("/api/connect", headers=CONTROL, json={"port": "loop://"}).json()
        assert first["session_id"] != second["session_id"]
        assert client.get("/api/sessions/not-found/records", headers=VIEW).status_code == 404


def test_unclean_shutdown_is_marked_incomplete(tmp_path):
    store = SessionStore(tmp_path)
    sid = store.begin({"port": "test"})
    store.append_batch([(sid, time.time(), time.monotonic_ns(), "rx", b"evidence")])
    store.close()
    recovered = SessionStore(tmp_path)
    session = recovered.sessions()[0]
    assert session["ended"] is not None and not session["complete"]
    assert "异常退出" in session["error"]
    assert base64.b64decode(recovered.read(sid)["records"][0]["data_base64"]) == b"evidence"
    recovered.close()


def test_disk_failure_stops_capture(tmp_path):
    app = create_app(tmp_path, "controller", "observer")
    with TestClient(app) as client:
        service = app.state.service
        client.post("/api/connect", headers=CONTROL, json={"port": "loop://"})
        def fail(batch):
            raise OSError("simulated disk full")
        service.store.append_batch = fail
        client.post("/api/send", headers=CONTROL, json={"data": "evidence"})
        deadline = time.monotonic() + 3
        while service.status()["state"] != "fault" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert service.status()["state"] == "fault"
        assert "写入失败" in service.status()["error"]
        assert client.post("/api/send", headers=CONTROL, json={"data": "retry"}).status_code == 409


def test_fault_then_disconnect_clears_status_and_keeps_session(tmp_path):
    app = create_app(tmp_path, "controller", "observer")
    with TestClient(app) as client:
        sid = client.post("/api/connect", headers=CONTROL,
                          json={"port": "loop://"}).json()["session_id"]
        app.state.service._fail("模拟故障")
        status = client.post("/api/disconnect", headers=CONTROL, json={}).json()
        assert status["state"] == "disconnected"
        assert status["error"] is None
        assert status["session_id"] == sid
        listed = {s["id"]: s for s in client.get("/api/sessions", headers=VIEW).json()}
        assert listed[sid]["complete"] is False
        assert listed[sid]["error"] == "模拟故障"
        assert client.get(f"/api/sessions/{sid}/records?limit=800", headers=VIEW).status_code == 200


def test_hex_error_message_is_localized(tmp_path):
    with TestClient(create_app(tmp_path, "controller", "observer")) as client:
        client.post("/api/connect", headers=CONTROL, json={"port": "loop://"})
        response = client.post("/api/send", headers=CONTROL, json={"data": "ZZ", "format": "hex"})
        assert response.status_code == 400
        assert "HEX 内容无效" in response.json()["detail"]


def test_connection_presets_are_recorded_deduplicated_and_removable(tmp_path):
    with TestClient(create_app(tmp_path, "controller", "observer")) as client:
        assert client.get("/api/presets", headers=VIEW).json() == []
        client.post("/api/connect", headers=CONTROL, json={"port": "loop://"})
        presets = client.get("/api/presets", headers=VIEW).json()
        assert len(presets) == 1
        assert presets[0]["config"]["port"] == "loop://"
        assert presets[0]["config"]["baudrate"] == 115200
        assert "loop://" in presets[0]["name"]
        client.post("/api/disconnect", headers=CONTROL, json={})
        client.post("/api/connect", headers=CONTROL, json={"port": "loop://"})
        assert len(client.get("/api/presets", headers=VIEW).json()) == 1
        saved = client.post("/api/presets", headers=CONTROL,
                            json={"name": "我的设备", "config": {"port": "loop://", "baudrate": 9600}})
        assert saved.status_code == 200 and saved.json()["name"] == "我的设备"
        assert len(client.get("/api/presets", headers=VIEW).json()) == 2
        pid = saved.json()["id"]
        assert client.delete(f"/api/presets/{pid}", headers=VIEW).status_code == 403
        assert client.delete(f"/api/presets/{pid}", headers=CONTROL).status_code == 200
        remaining = client.get("/api/presets", headers=VIEW).json()
        assert len(remaining) == 1 and remaining[0]["config"]["baudrate"] == 115200
        assert client.get("/api/status", headers=CONTROL).json()["presets"][0]["config"]["port"] == "loop://"


def test_session_writes_readable_log_and_raw_jsonl(tmp_path):
    app = create_app(tmp_path, "controller", "observer")
    with TestClient(app) as client:
        target = tmp_path / "my-logs"
        saved = client.post("/api/settings", headers=CONTROL, json={"output_dir": str(target)})
        assert saved.status_code == 200 and saved.json()["output_dir"] == str(target)
        assert client.get("/api/settings", headers=VIEW).json()["output_dir"] == str(target)
        connection = client.post("/api/connect", headers=CONTROL, json={"port": "loop://"}).json()
        log_path = Path(connection["log_file"])
        assert log_path.parent == target and log_path.suffix == ".log"
        client.post("/api/send", headers=CONTROL, json={"data": "hello", "ending": ""})
        client.post("/api/disconnect", headers=CONTROL, json={})
        text = log_path.read_text(encoding="utf-8")
        assert text.startswith("# DevToolNext")
        assert "TX  hello" in text and "RX  hello" in text
        assert text.rstrip().splitlines()[-1].startswith("# ended:")
        assert "complete=true" in text
        raw = log_path.with_suffix(".jsonl")
        assert raw.exists()
        rows = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
        assert rows[0]["type"] == "session" and rows[0]["session_id"] == connection["session_id"]
        assert rows[-1]["type"] == "end" and rows[-1]["complete"] is True
        sent = b"".join(base64.b64decode(row["data_base64"]) for row in rows
                        if row.get("direction") == "tx")
        assert sent == b"hello"
        assert client.get("/api/status", headers=VIEW).json()["log_file"] == str(log_path)


def test_live_endpoint_returns_new_records_immediately(tmp_path):
    with TestClient(create_app(tmp_path, "controller", "observer")) as client:
        sid = client.post("/api/connect", headers=CONTROL,
                          json={"port": "loop://"}).json()["session_id"]
        client.post("/api/send", headers=CONTROL, json={"data": "live", "ending": ""})
        started = time.monotonic()
        page = client.get(f"/api/sessions/{sid}/records/live?after=0&timeout=5",
                          headers=VIEW).json()
        assert time.monotonic() - started < 2
        assert page["state"] == "connected"
        sent = b"".join(base64.b64decode(row["data_base64"]) for row in page["records"]
                        if row["direction"] == "tx")
        assert sent == b"live"
        waited = time.monotonic()
        empty = client.get(f"/api/sessions/{sid}/records/live?after={page['next_cursor']}&timeout=1",
                           headers=VIEW).json()
        assert empty["records"] == []
        assert time.monotonic() - waited >= 0.9


def test_settings_validate_path_and_role(tmp_path):
    with TestClient(create_app(tmp_path, "controller", "observer")) as client:
        assert client.post("/api/settings", headers=CONTROL,
                           json={"output_dir": "logs"}).status_code == 400
        assert client.post("/api/settings", headers=VIEW,
                           json={"output_dir": str(tmp_path)}).status_code == 403
        assert client.get("/api/settings", headers=VIEW).json()["output_dir"].endswith("logs")


def test_line_control_needs_connection_and_control_role(tmp_path):
    with TestClient(create_app(tmp_path, "controller", "observer")) as client:
        assert client.post("/api/lines", headers=CONTROL, json={"dtr": True}).status_code == 409
        client.post("/api/connect", headers=CONTROL, json={"port": "loop://"})
        assert client.post("/api/lines", headers=VIEW, json={"dtr": True}).status_code == 403
        lines = client.post("/api/lines", headers=CONTROL, json={"dtr": False}).json()
        assert lines["dtr"] is False and lines["rts"] is True
        status = client.get("/api/status", headers=CONTROL).json()
        assert status["dtr"] is False and status["rts"] is True


def test_multiple_connections_are_independent(tmp_path):
    with TestClient(create_app(tmp_path, "controller", "observer")) as client:
        first = client.post("/api/connections", headers=CONTROL, json={"port": "loop://"}).json()
        second = client.post("/api/connections", headers=CONTROL, json={"port": "loop://"}).json()
        assert first["id"] != second["id"]
        assert first["session_id"] != second["session_id"]
        assert len(client.get("/api/connections", headers=VIEW).json()["connections"]) == 2
        client.post(f"/api/connections/{first['id']}/send", headers=CONTROL,
                    json={"data": "one", "ending": ""})
        client.post(f"/api/connections/{second['id']}/send", headers=CONTROL,
                    json={"data": "two", "ending": ""})
        time.sleep(0.3)

        def sent(sid):
            page = client.get(f"/api/sessions/{sid}/records", headers=VIEW).json()
            return b"".join(base64.b64decode(row["data_base64"]) for row in page["records"]
                            if row["direction"] == "tx")

        assert sent(first["session_id"]) == b"one"
        assert sent(second["session_id"]) == b"two"
        assert client.delete(f"/api/connections/{first['id']}", headers=CONTROL).status_code == 200
        left = client.get("/api/connections", headers=VIEW).json()["connections"]
        assert len(left) == 1 and left[0]["id"] == second["id"]
        assert left[0]["state"] == "connected"


def test_credentials_never_reach_storage(tmp_path):
    store = SessionStore(tmp_path)
    try:
        store.begin({"type": "ssh", "port": "host", "username": "u", "password": "secret"})
        assert "secret" not in json.dumps(store.sessions()[0]["config"])
        store.save_preset({"type": "ssh", "port": "host", "password": "secret"})
        assert "secret" not in json.dumps(store.presets())
    finally:
        store.close()


def test_second_process_cannot_recover_a_live_store(tmp_path):
    store = SessionStore(tmp_path)
    sid = store.begin({"port": "live-device"})
    try:
        with pytest.raises(RuntimeError, match="另一个"):
            SessionStore(tmp_path)
        assert store.sessions()[0]["ended"] is None
        store.finish(sid)
    finally:
        store.close()
