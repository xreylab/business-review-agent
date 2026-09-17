"""检索节点：调用 Tavily 按子任务联网搜索，汇总证据材料。"""

from __future__ import annotations

from typing import Any

from app.graph.state import AgentState, AgentStateUpdate
from app.tools.tavily_search import tavily_search


def _format_one_result(item: Any) -> str:
    """把单条 Tavily 结果格式化成可读文本。"""

    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return str(item)

    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    content = str(item.get("content") or item.get("snippet") or "").strip()
    parts = [part for part in (title, url, content) if part]
    return " | ".join(parts) if parts else str(item)


def _format_payload(task: str, payload: Any) -> str:
    """把一次检索的原始返回整理成写入 search_results 的字符串。"""

    if isinstance(payload, str):
        return f"【子任务】{task}\n{payload.strip()}"

    results: list[Any]
    if isinstance(payload, dict):
        raw_results = payload.get("results", payload)
        results = raw_results if isinstance(raw_results, list) else [payload]
    elif isinstance(payload, list):
        results = payload
    else:
        results = [payload]

    lines = [_format_one_result(item) for item in results if item]
    body = "\n".join(f"- {line}" for line in lines) if lines else "（无检索结果）"
    return f"【子任务】{task}\n{body}"


def search(state: AgentState) -> AgentStateUpdate:
    """遍历 task_list，调用封装好的 Tavily 工具，结果写入 search_results。"""

    search_results: list[str] = []
    for task in state.get("task_list") or []:
        query = task.strip()
        if not query:
            continue
        try:
            payload = tavily_search.invoke({"query": query})
            search_results.append(_format_payload(query, payload))
        except Exception as exc:  # 单条失败不中断整轮检索
            search_results.append(f"【子任务】{query}\n检索失败：{exc}")

    return {"search_results": search_results}
