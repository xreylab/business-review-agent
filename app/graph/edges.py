"""条件边：自检不合格则返工；人工介入后决定继续 / 修改 / 结束。"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END

from app.graph.nodes.review import is_report_qualified
from app.graph.state import MAX_RETRY_COUNT, AgentState

# 路由目标常量（与 builder 中节点名、Literal 注解保持一致）
ROUTE_WRITE_REPORT = "write_report"
ROUTE_HUMAN_REVIEW = "human_review"
ROUTE_REVIEW = "review"

AfterReviewRoute = Literal["write_report", "human_review"]
AfterHumanReviewRoute = Literal["review", "__end__"]


def route_after_review(state: AgentState) -> AfterReviewRoute:
    """review 节点之后的条件路由。

    规则：
    - 报告不合格，且 retry_count < 2 → 回到 write_report（重试次数在 write_report 中 +1）
    - 报告合格，或已达最大重试次数 → 进入 human_review
    """

    comments = state.get("review_comments") or ""
    retry_count = int(state.get("retry_count") or 0)

    if not is_report_qualified(comments) and retry_count < MAX_RETRY_COUNT:
        return ROUTE_WRITE_REPORT

    return ROUTE_HUMAN_REVIEW


def route_after_human_review(state: AgentState) -> AfterHumanReviewRoute:
    """human_review 节点之后的条件路由。

    规则：
    - 用户确认通过 → END
    - 用户修改报告 → 返回 review 重新自检
    """

    decision = (state.get("human_decision") or "").strip().lower()
    if decision == "revise":
        return ROUTE_REVIEW
    return END
