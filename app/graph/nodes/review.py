"""自检节点：校验报告完整性与证据覆盖；不合格则进入返工循环。"""

from __future__ import annotations

import re

from langchain_core.output_parsers import StrOutputParser

from app.graph.state import AgentState, AgentStateUpdate
from app.llm.deepseek import llm
from app.prompts.templates import review_prompt

_VERDICT_RE = re.compile(r"结论\s*[:：]\s*(不合格|合格)")


def is_report_qualified(review_text: str) -> bool:
    """从评审原文解析是否合格。优先匹配「结论：」行，避免「不合格」被当成「合格」。"""

    matched = _VERDICT_RE.search(review_text or "")
    if matched:
        return matched.group(1) == "合格"
    # 兜底：先看不合格，再看合格
    if "不合格" in (review_text or ""):
        return False
    return "合格" in (review_text or "")


def _join_lines(items: list[str] | None) -> str:
    if not items:
        return "（暂无）"
    return "\n".join(str(item) for item in items if str(item).strip())


def review(state: AgentState) -> AgentStateUpdate:
    """读取 report，调用 llm + review_prompt，写入 review_comments。

    是否返工由条件边根据「合格 / 不合格」与 retry_count 决定；
    重试次数在写回 write_report 时递增（条件边本身不能改 state）。
    """

    chain = review_prompt | llm | StrOutputParser()
    review_comments = chain.invoke(
        {
            "user_query": state.get("user_query") or "",
            "search_results": _join_lines(state.get("search_results")),
            "report": state.get("report") or "",
        }
    ).strip()

    return {
        "review_comments": review_comments,
        # 清掉上一轮人工决策，避免误路由
        "human_decision": "",
    }
