"""复用原 SQL 节点的无会话记忆子图，每次调用均为独立任务。"""
from langgraph.graph import StateGraph, START, END
from agent.state import AskDataState


def build_query_agent(nodes, max_attempts=3):
    graph = StateGraph(AskDataState)
    for name in ("retrieve_node", "generate_sql_node", "execute_node", "repair_node"):
        graph.add_node(name, nodes[name])
    graph.add_edge(START, "retrieve_node")
    graph.add_edge("retrieve_node", "generate_sql_node")
    graph.add_edge("generate_sql_node", "execute_node")

    def after_execute(state):
        if state["success"] or state["attempts"] + 1 >= max_attempts:
            return "done"
        # 安全拒绝不应通过重试绕过。
        if "安全拦截" in (state.get("last_error") or ""):
            return "done"
        return "repair"

    graph.add_conditional_edges("execute_node", after_execute,
                                {"done": END, "repair": "repair_node"})
    graph.add_edge("repair_node", "generate_sql_node")
    return graph.compile()
