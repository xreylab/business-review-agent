"""Human-in-the-loop 节点：在终端暂停，等待人工确认或修改报告。"""

from __future__ import annotations

from app.graph.state import AgentState, AgentStateUpdate

_END_TOKEN = "END"


def _read_multiline(first_line: str) -> str:
    """从终端读取多行报告，直到单独一行 END。"""

    lines = [first_line]
    print(f"继续输入报告正文，单独一行 {_END_TOKEN} 结束：")
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == _END_TOKEN:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def human_review(state: AgentState) -> AgentStateUpdate:
    """HITL：在终端展示当前报告，回车确认，或输入新文本替换 state.report。

    返回 human_decision：
    - approve：用户确认通过 → 条件边走向 END
    - revise：用户修改报告 → 条件边回到 review 重新自检
    """

    report = state.get("report") or ""
    comments = state.get("review_comments") or "（无）"

    print("\n" + "=" * 60)
    print("[人工审核] 请审阅当前业务报告")
    print("-" * 60)
    print(report or "（当前报告为空）")
    print("-" * 60)
    print("自检意见：")
    print(comments)
    print("=" * 60)
    print("操作：直接回车 = 确认当前报告；输入新内容后单独一行 END 提交修改。")

    try:
        first_line = input("> ")
    except EOFError:
        return {"human_decision": "approve"}

    if not first_line.strip() or first_line.strip() == _END_TOKEN:
        return {"human_decision": "approve"}

    new_report = _read_multiline(first_line)
    if not new_report:
        return {"human_decision": "approve"}

    return {
        "report": new_report,
        "human_decision": "revise",
    }
