from __future__ import annotations
from langgraph.graph import StateGraph,START,END
from langgraph.checkpoint.memory import MemorySaver
from agent.state import AskDataState
from agent.nodes import build_nodes

def build_graph(model_client,hybrid,executor,max_attempts:int = 3):
    """
    构建并编译LangGraph Text2SQL自修复Agent图
    :param model_client: LLM客户端实例
    :param hybrid: 混合检索器 HybridRetriever
    :param executor: SQL执行器 SQLExecutor
    :param max_attempts: SQL最大重试次数
    :return: compiled_graph: 编译完成的langgraph可执行图    
    """

    # 1.实例化状态图
    graph_builder = StateGraph(AskDataState)

    # 2.闭包生成全部节点函数，注入外部依赖
    node_map = build_nodes(model_client,hybrid,executor)

    # 注册所有节点到图中
    graph_builder.add_node("route_node", node_map["route_node"])
    graph_builder.add_node("chat_node", node_map["chat_node"])
    graph_builder.add_node("retrieve_node", node_map["retrieve_node"])
    graph_builder.add_node("generate_sql_node", node_map["generate_sql_node"])
    graph_builder.add_node("execute_node", node_map["execute_node"])
    graph_builder.add_node("repair_node", node_map["repair_node"])
    graph_builder.add_node("answer_node", node_map["answer_node"])

    # ========== 路由判断函数 ==========   
    def route_router(state:AskDataState) -> str:
        """
        路由分支：根据state["route"]决定走闲聊还是数据查询
        """
        return state.get("route") or "data"

    def after_execute_router(state:AskDataState) -> str:
        """
        SQL执行后的条件分支
        1. success=True → 直接到answer_node
        2. success=False 且 已执行次数(attempts+1) < max_attempts → repair_node（计数，回到生成SQL）
        3. success=False 且 次数耗尽 → answer_node，返回失败提示
        注意：attempts在repair_node中+1，路由时 attempts+1 = 已执行的总尝试次数
        """
        if state["success"]:
            return "answer_node"
        if state["attempts"] + 1 < max_attempts:
            return "repair_node"
        return "answer_node"

    # ========== 构建边关系 ==========
    # 入口
    graph_builder.add_edge(START,"route_node")

    # 路由节点的条件分支
    graph_builder.add_conditional_edges(
        "route_node",
        route_router,
        {
            "chat":"chat_node",
            "data":"retrieve_node"
        }
    )

    # 闲聊直接结束
    graph_builder.add_edge("chat_node",END)

    # 数据查询链路
    graph_builder.add_edge("retrieve_node","generate_sql_node")
    graph_builder.add_edge("generate_sql_node","execute_node")

    # 执行后的条件分支
    graph_builder.add_conditional_edges(
        "execute_node",
        after_execute_router,
        {
            "answer_node":"answer_node",
            "repair_node":"repair_node"
        }
    )

    # 重试簿记节点 → 回到SQL生成节点，形成循环
    graph_builder.add_edge("repair_node","generate_sql_node")

    # 最终回答节点结束
    graph_builder.add_edge("answer_node",END)

    # 使用内存检查点，支持会话线程
    checkpointer = MemorySaver()
    compiled_graph = graph_builder.compile(checkpointer=checkpointer)

    return compiled_graph

def export_mermaid_diagram(graph,save_path:str = "agent_graph.mmd"):
    """
    导出Mermaid流程图文本，保存到文件，用于README展示
    :param graph: 编译后的langgraph图
    :param save_path: mermaid文件保存路径
    """
    mermaid_text = graph.get_graph().draw_mermaid()
    with open(save_path,"w",encoding="utf-8") as f:
        f.write(mermaid_text)
    print(f"Mermaid流程图已导出至 {save_path}")