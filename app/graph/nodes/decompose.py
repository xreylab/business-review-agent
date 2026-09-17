"""任务拆解节点：把调研 / 复盘 / 工作总结需求拆成可检索子任务。"""

from __future__ import annotations

import re

from langchain_core.output_parsers import StrOutputParser

from app.graph.state import AgentState, AgentStateUpdate
from app.llm.deepseek import llm
from app.prompts.templates import decompose_prompt

# 匹配「1. xxx」「1、xxx」「- xxx」等列表项
_TASK_LINE_RE = re.compile(r"^\s*(?:\d+[\.、\)）]|[-*•])\s*(.+?)\s*$")


def _parse_task_list(raw_text: str) -> list[str]:
    """把模型输出的编号列表解析成干净的子任务字符串。"""

    tasks: list[str] = []
    for line in raw_text.splitlines():
        matched = _TASK_LINE_RE.match(line)
        if matched:
            item = matched.group(1).strip()
            if item:
                tasks.append(item)
            continue
        # 没有编号但非空的行，作为兜底保留
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "子任务", "任务拆解")):
            tasks.append(stripped)
    return tasks


def decompose(state: AgentState) -> AgentStateUpdate:
    """读取 user_query，调用 llm + decompose_prompt，把 task_list 写回 state。"""

    chain = decompose_prompt | llm | StrOutputParser()
    raw_text = chain.invoke({"user_query": state["user_query"]})
    task_list = _parse_task_list(raw_text)

    return {"task_list": task_list}
