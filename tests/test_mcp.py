import json
import time

from fastapi.testclient import TestClient

from devtoolnext.server import create_app

CONTROL = {"Authorization": "Bearer controller"}
VIEW = {"Authorization": "Bearer observer"}


def rpc(client, headers, method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", headers={**headers, "Content-Type": "application/json"}, json=body)


def payload(response):
    return json.loads(response.json()["result"]["content"][0]["text"])


def test_mcp_requires_token(tmp_path):
    with TestClient(create_app(tmp_path, "controller", "observer")) as client:
        assert rpc(client, {}, "initialize", {"protocolVersion": "2025-06-18"}).status_code == 401
        assert client.post("/mcp?token=controller",
                           json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 200


def test_mcp_can_be_switched_off(tmp_path):
    with TestClient(create_app(tmp_path, "controller", "observer")) as client:
        assert client.get("/api/settings", headers=CONTROL).json()["mcp_enabled"] is True
        saved = client.post("/api/settings", headers=CONTROL, json={"mcp_enabled": False}).json()
        assert saved["mcp_enabled"] is False
        assert rpc(client, CONTROL, "initialize", {"protocolVersion": "2025-06-18"}).status_code == 404
        client.post("/api/settings", headers=CONTROL, json={"mcp_enabled": True})
        assert rpc(client, CONTROL, "initialize", {"protocolVersion": "2025-06-18"}).status_code == 200


def test_mcp_tools_drive_serial_loopback(tmp_path):
    with TestClient(create_app(tmp_path, "controller", "observer")) as client:
        init = rpc(client, CONTROL, "initialize",
                   {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"}})
        assert init.status_code == 200
        assert init.json()["result"]["serverInfo"]["name"] == "devtoolnext"
        assert init.headers.get("Mcp-Session-Id")
        assert rpc(client, CONTROL, "ping").json()["result"] == {}

        names = {tool["name"] for tool in rpc(client, CONTROL, "tools/list").json()["result"]["tools"]}
        assert {"open_connection", "send_command", "read_output", "wait_for_output",
                "list_connections", "close_connection"} <= names

        info = payload(rpc(client, CONTROL, "tools/call",
                           {"name": "open_connection", "arguments": {"type": "serial", "port": "loop://"}}))
        cid = info["id"]
        assert info["state"] == "connected" and info["target"].startswith("loop://")
        assert [item["id"] for item in payload(rpc(client, CONTROL, "tools/call",
                                                   {"name": "list_connections"}))] == [cid]

        sent = payload(rpc(client, CONTROL, "tools/call",
                           {"name": "send_command",
                            "arguments": {"connection_id": cid, "data": "hello", "ending": "crlf"}}))
        assert sent["written"] == 7

        waited = payload(rpc(client, CONTROL, "tools/call",
                             {"name": "wait_for_output",
                              "arguments": {"connection_id": cid, "pattern": "hello",
                                            "after": sent["cursor"], "timeout": 5}}))
        assert waited["matched"] is True and "hello" in waited["text"]

        read = payload(rpc(client, CONTROL, "tools/call",
                           {"name": "read_output", "arguments": {"connection_id": cid, "after": 0}}))
        assert any("hello" in row["text"] for row in read["records"])

        denied = rpc(client, VIEW, "tools/call",
                     {"name": "send_command", "arguments": {"connection_id": cid, "data": "x"}}).json()["result"]
        assert denied["isError"] is True

        closed = payload(rpc(client, CONTROL, "tools/call",
                             {"name": "close_connection", "arguments": {"connection_id": cid}}))
        assert closed["closed"] == cid
        assert payload(rpc(client, CONTROL, "tools/call", {"name": "list_connections"})) == []


def test_mcp_wait_times_out_without_matching_output(tmp_path):
    with TestClient(create_app(tmp_path, "controller", "observer")) as client:
        info = payload(rpc(client, CONTROL, "tools/call",
                           {"name": "open_connection", "arguments": {"type": "serial", "port": "loop://"}}))
        started = time.monotonic()
        result = payload(rpc(client, CONTROL, "tools/call",
                             {"name": "wait_for_output",
                              "arguments": {"connection_id": info["id"], "pattern": "never", "timeout": 1}}))
        assert result["matched"] is False
        assert time.monotonic() - started >= 0.9
