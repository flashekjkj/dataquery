"""Smoke-test the deployed HTTP service; calls the configured paid model APIs."""
from __future__ import annotations

import argparse
import json
import urllib.request
import uuid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--check-session", help="Only check that a session survived restart")
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path: str, body: dict | None = None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            args.base_url.rstrip("/") + path,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        return opener.open(req, timeout=600)

    def api(path: str, body: dict | None = None):
        with request(path, body) as response:
            return json.load(response)

    if args.check_session:
        session = api("/api/sessions/" + args.check_session)
        assert len(session["turns"]) >= 2, "Session history missing"
        print("PASS: session persisted after restart")
        return

    with request("/") as response:
        assert b'<div id="app"' in response.read(), "Frontend HTML missing"
    health = api("/api/health")
    assert health["agent_ready"] is True, health
    assert len(api("/api/skills")) >= 2, "Skills missing"
    print("PASS: frontend, health and skills")

    chat = api("/api/chat", {"question": "你好", "session_id": "docker-chat-" + uuid.uuid4().hex})
    assert chat["route"] == "chat" and chat["answer"], "Chat failed"
    print("PASS: chat")

    session_id = "docker-query-" + uuid.uuid4().hex
    query = api("/api/chat", {"question": "各地区的实付金额合计是多少", "session_id": session_id})
    assert query["route"] == "data" and query["success"], query.get("answer")
    assert query["query_result"]["row_count"] > 0, "No query rows returned"
    assert query["final_sql"] and query["answer"], "SQL or answer missing"
    print("PASS: database query")
    print("PERSISTENCE_SESSION=" + session_id)

    tool = api("/api/chat", {"question": "帮我计算 3*5+2", "session_id": "docker-tool-" + uuid.uuid4().hex})
    assert tool["route"] == "tool", "Tool route not selected"
    assert "calculator__calculate" in tool["tool_calls"], "Calculator not selected"
    assert "17" in tool["tool_result"] and tool["answer"], "Calculator failed"
    print("PASS: MCP calculator")

    events = []
    with request("/api/chat/stream", {"question": "你好", "session_id": "docker-sse-" + uuid.uuid4().hex}) as response:
        assert "text/event-stream" in response.headers["Content-Type"]
        for line in response:
            if line.startswith(b"data: "):
                events.append(json.loads(line[6:]))
    assert any(event["type"] == "node" for event in events), "No node events"
    assert any(event["type"] == "done" for event in events), "No done event"
    assert not any(event["type"] == "error" for event in events), "SSE error"
    print("PASS: SSE through Nginx")


if __name__ == "__main__":
    main()
