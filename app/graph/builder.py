"""图编译入口：组装节点/边，接入 InMemorySaver checkpoint，支持人工介入后的条件路由。"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer

from app.graph.edges import (
    ROUTE_HUMAN_REVIEW,
    ROUTE_REVIEW,
    ROUTE_WRITE_REPORT,
    route_after_human_review,
    route_after_review,
)
from app.graph.nodes import (
    decompose,
    human_review,
    review,
    search,
    write_report,
)
from app.graph.state import AgentState

# 编译后图的标准类型：StateT = AgentState
CompiledReviewGraph = CompiledStateGraph[AgentState]


def build_graph(
    *,
    checkpointer: Checkpointer | None = None,
    interrupt_before: list[str] | None = None,
    interrupt_after: list[str] | None = None,
) -> CompiledReviewGraph:
    """构建并编译业务复盘 Agent 图（LangGraph V1）。

    流转保持不变：
    START → decompose → search → write_report → review
         不合格且 retry_count < 2 ↺ write_report
         合格 / 达最大重试 → human_review → END 或回 review

    checkpointer 默认 InMemorySaver；CLI 可注入 SqliteSaver 以跨进程恢复 thread_id。
    interrupt_before / interrupt_after 供 HITL 断点测试与人工介入暂停。
    """

    workflow: StateGraph[AgentState] = StateGraph(AgentState)

    # ---- 注册节点 ----
    workflow.add_node("decompose", decompose)
    workflow.add_node("search", search)
    workflow.add_node("write_report", write_report)
    workflow.add_node("review", review)
    workflow.add_node("human_review", human_review)

    # ---- 固定边：入口 decompose → search → write_report → review ----
    workflow.add_edge(START, "decompose")
    workflow.add_edge("decompose", "search")
    workflow.add_edge("search", "write_report")
    workflow.add_edge("write_report", "review")

    # ---- 条件边：自检后决定返工或人工审核 ----
    workflow.add_conditional_edges(
        "review",
        route_after_review,
        {
            ROUTE_WRITE_REPORT: "write_report",
            ROUTE_HUMAN_REVIEW: "human_review",
        },
    )

    # ---- 条件边：人工确认结束，或修改后重回自检 ----
    workflow.add_conditional_edges(
        "human_review",
        route_after_human_review,
        {
            ROUTE_REVIEW: "review",
            END: END,
        },
    )

    # 未注入时使用 InMemorySaver；CLI 可换成 SqliteSaver 做断点续跑
    saver = checkpointer if checkpointer is not None else InMemorySaver()
    return workflow.compile(
        checkpointer=saver,
        name="business-review-agent",
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
    )


def render_mermaid(graph: CompiledReviewGraph | None = None) -> str:
    """导出编译图的 Mermaid 文本，便于简历演示与文档嵌入。"""

    compiled = graph if graph is not None else build_graph()
    return compiled.get_graph().draw_mermaid()


if __name__ == "__main__":
    print(render_mermaid())
