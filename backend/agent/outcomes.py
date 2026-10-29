"""传输完成与业务完成分开计量，REST/SSE 使用同一规则。"""


def classify_outcome(state):
    status = state.get("collaboration_status")
    if status in ("partial", "clarification"):
        return status
    if state.get("degraded"):
        return "degraded"
    if state.get("route") == "tool":
        return "success" if state.get("tool_success") else "failed"
    if state.get("route") == "chat":
        answer = state.get("answer") or ""
        return "degraded" if "模型服务暂时不可用" in answer else "success"
    return "success" if state.get("success") else "failed"
