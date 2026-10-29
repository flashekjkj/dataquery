"""复用原 SQL 节点的无会话记忆子图，每次调用均为独立任务。"""
from langgraph.graph import StateGraph, START, END
from agent.state import AskDataState
from agent.policies import should_repair_sql


def build_query_agent(nodes, max_attempts=3):
    graph = StateGraph(AskDataState)
    for name in ("retrieve_node", "generate_sql_node", "execute_node", "repair_node"):
        graph.add_node(name, nodes[name])
    graph.add_edge(START, "retrieve_node")
    graph.add_edge("retrieve_node", "generate_sql_node")
    graph.add_edge("generate_sql_node", "execute_node")

    def after_execute(state):
        return "repair" if should_repair_sql(state, max_attempts) else "done"

    graph.add_conditional_edges("execute_node", after_execute,
                                {"done": END, "repair": "repair_node"})
    graph.add_edge("repair_node", "generate_sql_node")
    return graph.compile()
