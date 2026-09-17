"""图流程测试：编译、条件边可视化、Mermaid 导出（不调用 LLM / 搜索）。"""

from app.graph.builder import build_graph, render_mermaid
from app.graph.edges import route_after_human_review, route_after_review
from app.graph.state import AgentState


def _empty_state(**overrides: object) -> AgentState:
    state: AgentState = {
        "user_query": "",
        "task_list": [],
        "search_results": [],
        "report": "",
        "review_comments": "",
        "retry_count": 0,
        "human_decision": "",
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def test_build_graph_compiles() -> None:
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


def test_route_after_review_retry_and_hitl() -> None:
    fail_once = _empty_state(review_comments="结论：不合格", retry_count=0)
    assert route_after_review(fail_once) == "write_report"

    fail_max = _empty_state(review_comments="结论：不合格", retry_count=2)
    assert route_after_review(fail_max) == "human_review"

    passed = _empty_state(review_comments="结论：合格", retry_count=0)
    assert route_after_review(passed) == "human_review"


def test_route_after_human_review() -> None:
    revised = _empty_state(human_decision="revise")
    assert route_after_human_review(revised) == "review"

    approved = _empty_state(human_decision="approve")
    assert route_after_human_review(approved) == "__end__"
