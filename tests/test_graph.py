"""图流程 pytest：编译、返工循环（最多 2 次）、interrupt 暂停与恢复。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from app.graph.builder import build_graph, render_mermaid
from app.graph.edges import route_after_human_review, route_after_review
from app.graph.nodes.review import is_report_qualified
from app.graph.state import MAX_RETRY_COUNT, AgentState, AgentStateUpdate

SAMPLE_QUERY = "调研2026大模型Agent企业落地方案"


def _initial_state(query: str = SAMPLE_QUERY) -> AgentState:
    return {
        "user_query": query,
        "task_list": [],
        "search_results": [],
        "report": "",
        "review_comments": "",
        "retry_count": 0,
        "human_decision": "",
    }


def _node_names_from_chunk(chunk: Any) -> list[str]:
    """兼容 stream_mode='updates' 的 dict / (mode, data) / {type,data} 三种块格式。"""

    if isinstance(chunk, tuple) and len(chunk) == 2:
        mode, data = chunk
        if mode == "updates" and isinstance(data, dict):
            return [str(name) for name in data]
        return []
    if isinstance(chunk, dict) and chunk.get("type") == "updates":
        data = chunk.get("data") or {}
        return [str(name) for name in data] if isinstance(data, dict) else []
    if isinstance(chunk, dict):
        return [str(name) for name in chunk]
    return []


def _visited_nodes(graph: Any, payload: AgentState, config: dict[str, Any]) -> list[str]:
    visited: list[str] = []
    for chunk in graph.stream(payload, config, stream_mode="updates"):
        visited.extend(_node_names_from_chunk(chunk))
    return visited


@contextmanager
def _patched_graph(
    *,
    review_comments: list[str] | str,
    interrupt_before: list[str] | None = None,
    human_decision: str = "approve",
) -> Iterator[tuple[Any, dict[str, int]]]:
    """用假节点编译图，避免真实 LLM / Tavily / 终端 input。"""

    counters = {"write": 0, "review": 0, "human": 0}
    comments_queue = (
        [review_comments] if isinstance(review_comments, str) else list(review_comments)
    )

    def fake_decompose(state: AgentState) -> AgentStateUpdate:
        return {"task_list": ["拆解子任务1", "拆解子任务2"]}

    def fake_search(state: AgentState) -> AgentStateUpdate:
        return {"search_results": ["模拟检索结果"]}

    def fake_write_report(state: AgentState) -> AgentStateUpdate:
        counters["write"] += 1
        update: AgentStateUpdate = {
            "report": f"模拟报告 v{counters['write']}",
            "human_decision": "",
        }
        prev_comments = state.get("review_comments") or ""
        if prev_comments and not is_report_qualified(prev_comments):
            update["retry_count"] = int(state.get("retry_count") or 0) + 1
        return update

    def fake_review(state: AgentState) -> AgentStateUpdate:
        counters["review"] += 1
        if comments_queue:
            comments = comments_queue.pop(0)
        else:
            comments = "结论：不合格"
        return {"review_comments": comments, "human_decision": ""}

    def fake_human_review(state: AgentState) -> AgentStateUpdate:
        counters["human"] += 1
        return {"human_decision": human_decision}  # type: ignore[typeddict-item]

    with (
        patch("app.graph.builder.decompose", fake_decompose),
        patch("app.graph.builder.search", fake_search),
        patch("app.graph.builder.write_report", fake_write_report),
        patch("app.graph.builder.review", fake_review),
        patch("app.graph.builder.human_review", fake_human_review),
    ):
        graph = build_graph(
            checkpointer=InMemorySaver(),
            interrupt_before=interrupt_before,
        )
        yield graph, counters


def test_graph_compiles() -> None:
    """1. 图可以正常编译，并包含全部业务节点。"""

    graph = build_graph()
    mermaid = render_mermaid(graph)
    for node_name in (
        "decompose",
        "search",
        "write_report",
        "review",
        "human_review",
    ):
        assert node_name in mermaid
    assert graph.get_graph() is not None


def test_retry_loop_max_two_rewrites() -> None:
    """2. 模拟持续不合格：write_report 最多返工 2 次（共撰写 3 稿）后进入人工审核。"""

    config = {"configurable": {"thread_id": "retry-max-2"}}
    with _patched_graph(review_comments="结论：不合格") as (graph, counters):
        visited = _visited_nodes(graph, _initial_state(), config)
        snapshot = graph.get_state(config)

    assert visited.count("write_report") == MAX_RETRY_COUNT + 1
    assert counters["write"] == MAX_RETRY_COUNT + 1
    assert counters["review"] == MAX_RETRY_COUNT + 1
    assert counters["human"] == 1
    assert snapshot.values["retry_count"] == MAX_RETRY_COUNT
    assert snapshot.next == ()
    assert "human_review" in visited
    # 达上限后不应再写第 4 稿
    assert visited.count("write_report") <= 3


def test_retry_loop_recovers_before_max() -> None:
    """2b. 第一次不合格、第二次合格：只返工 1 次，不打满 2 次。"""

    config = {"configurable": {"thread_id": "retry-once"}}
    with _patched_graph(
        review_comments=["结论：不合格", "结论：合格"],
    ) as (graph, counters):
        visited = _visited_nodes(graph, _initial_state(), config)
        snapshot = graph.get_state(config)

    assert counters["write"] == 2
    assert snapshot.values["retry_count"] == 1
    assert "human_review" in visited
    assert snapshot.next == ()


def test_interrupt_human_review_pause_and_resume() -> None:
    """3. interrupt_before=human_review 时可暂停，invoke(None) 后可恢复并结束。"""

    config = {"configurable": {"thread_id": "hitl-interrupt"}}
    with _patched_graph(
        review_comments="结论：合格",
        interrupt_before=["human_review"],
    ) as (graph, counters):
        graph.invoke(_initial_state(), config)
        paused = graph.get_state(config)

        assert paused.next == ("human_review",)
        assert counters["human"] == 0
        assert (paused.values.get("report") or "").startswith("模拟报告")

        graph.invoke(None, config)
        resumed = graph.get_state(config)

    assert resumed.next == ()
    assert counters["human"] == 1
    assert resumed.values.get("human_decision") == "approve"


def test_route_after_review_retry_and_hitl() -> None:
    fail_once = _initial_state()
    fail_once["review_comments"] = "结论：不合格"
    fail_once["retry_count"] = 0
    assert route_after_review(fail_once) == "write_report"

    fail_max = _initial_state()
    fail_max["review_comments"] = "结论：不合格"
    fail_max["retry_count"] = 2
    assert route_after_review(fail_max) == "human_review"

    passed = _initial_state()
    passed["review_comments"] = "结论：合格"
    assert route_after_review(passed) == "human_review"


def test_route_after_human_review() -> None:
    revised = _initial_state()
    revised["human_decision"] = "revise"
    assert route_after_human_review(revised) == "review"

    approved = _initial_state()
    approved["human_decision"] = "approve"
    assert route_after_human_review(approved) == "__end__"
