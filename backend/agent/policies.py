"""父图和查询子图共用的 SQL 重试终止规则。"""


def should_repair_sql(state, max_attempts):
    if state.get("success") or state.get("llm_error"):
        return False
    if any(marker in (state.get("last_error") or "") for marker in ("安全拦截", "查询超时")):
        return False
    return state.get("attempts", 0) + 1 < max_attempts
