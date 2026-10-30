"""Bridge the MCP stdio transport to a running DevToolNext instance."""
import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 180


def _error(msg_id, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": message}}


def run(url: str, token: str) -> None:
    endpoint = url.rstrip("/") + "/mcp"
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            sys.stderr.write("忽略无法解析的输入\n")
            continue
        notification = isinstance(message, dict) and "id" not in message
        request = urllib.request.Request(
            endpoint, data=json.dumps(message).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read().decode("utf-8").strip()
        except urllib.error.HTTPError as exc:
            sys.stderr.write(f"DevToolNext MCP 返回 HTTP {exc.code}\n")
            if not notification:
                sys.stdout.write(json.dumps(_error(message.get("id"), f"HTTP {exc.code}")) + "\n")
                sys.stdout.flush()
            continue
        except OSError as exc:
            sys.stderr.write(f"无法连接 {endpoint}: {exc}\n")
            if not notification:
                sys.stdout.write(json.dumps(_error(message.get("id"), f"无法连接 {endpoint}")) + "\n")
                sys.stdout.flush()
            continue
        if notification or not body:
            continue
        sys.stdout.write(body + "\n")
        sys.stdout.flush()
