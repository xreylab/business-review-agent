"""图状态（AgentState）：在节点间传递用户需求、子任务、检索结果、报告与校验结论。"""

from __future__ import annotations

from typing import Literal, TypedDict

# 报告自检不合格时允许重写的最大次数（超过则不再返工）
MAX_RETRY_COUNT = 2

# 人工审核决策：空串表示尚未决策 / 已清空
HumanDecision = Literal["", "approve", "revise"]


class AgentState(TypedDict):
    """业务复盘 Agent 的共享状态，由各图节点读写。"""

    user_query: str  # 用户原始业务需求
    task_list: list[str]  # 拆解后的子任务列表
    search_results: list[str]  # Tavily 检索结果
    report: str  # 当前业务报告
    review_comments: str  # 自检评审意见
    retry_count: int  # 报告重试次数，最大 2 次
    human_decision: HumanDecision  # 人工审核决策：approve / revise


class AgentStateUpdate(TypedDict, total=False):
    """节点返回的部分状态更新（LangGraph V1：State -> Partial[State]）。"""

    user_query: str
    task_list: list[str]
    search_results: list[str]
    report: str
    review_comments: str
    retry_count: int
    human_decision: HumanDecision
