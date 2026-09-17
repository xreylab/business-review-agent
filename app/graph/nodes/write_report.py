"""撰写节点：基于检索材料生成业务复盘 / 调研报告。"""

from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser

from app.graph.nodes.review import is_report_qualified
from app.graph.state import AgentState, AgentStateUpdate
from app.llm.deepseek import llm
from app.prompts.templates import write_report_prompt


def _join_lines(items: list[str] | None) -> str:
    """把列表字段拼成 Prompt 可读文本。"""

    if not items:
        return "（暂无）"
    return "\n".join(f"- {item}" for item in items if str(item).strip())


def write_report(state: AgentState) -> AgentStateUpdate:
    """读取 task_list + search_results，调用 llm 和 write_report_prompt，写入 report。

    若带着上一轮「不合格」评审意见返工，则在此将 retry_count + 1
    （对应路由规则：不合格且 retry_count < 2 → 回写报告并递增重试次数）。
    """

    chain = write_report_prompt | llm | StrOutputParser()
    report = chain.invoke(
        {
            "user_query": state.get("user_query") or "",
            "task_list": _join_lines(state.get("task_list")),
            "search_results": _join_lines(state.get("search_results")),
            "review_comments": state.get("review_comments") or "（无）",
        }
    )

    update: AgentStateUpdate = {
        "report": report.strip(),
        "human_decision": "",
    }

    prev_comments = state.get("review_comments") or ""
    if prev_comments and not is_report_qualified(prev_comments):
        update["retry_count"] = int(state.get("retry_count") or 0) + 1

    return update
