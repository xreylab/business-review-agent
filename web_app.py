"""业务复盘 Agent — Streamlit Web 入口（零侵入交互层）。

本文件是唯一新增的 Web 层，不修改 app/ 下任何节点、graph、测试或 CLI。

复用能力：
- app.main：配置校验、初始 state、SQLite checkpoint、thread 恢复、报告落盘、友好错误
- app.graph.builder.build_graph：同一张业务图；Web 侧仅额外传入 interrupt_before，
  以便在 human_review 之前暂停，用网页替代终端 input()，路由规则仍走原条件边

启动（在项目根目录）：
    pip install streamlit
    streamlit run web_app.py
"""

from __future__ import annotations

import uuid
from typing import Any

# ---------------------------------------------------------------------------
# 依赖检查：不改 requirements.txt，缺失时给出安装提示
# ---------------------------------------------------------------------------
try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "未安装 streamlit。请在项目虚拟环境执行：pip install streamlit\n"
        "然后运行：streamlit run web_app.py"
    ) from exc

from app.graph.builder import CompiledReviewGraph, build_graph
from app.graph.state import AgentState
from app.main import (
    CHECKPOINT_DB,
    EXAMPLE_QUERY,
    _collect_network_error_types,
    _friendly_error,
    _initial_state,
    _make_sqlite_checkpointer,
    _preview,
    _resolve_payload,
    _save_report,
    _validate_settings,
)

# 流程展示名称（与现有节点一一对应，不改节点本身）
STEP_LABELS: dict[str, str] = {
    "decompose": "任务拆解",
    "search": "联网搜索",
    "write_report": "报告撰写",
    "review": "自检评审",
    "human_review": "人工审核",
}
STEP_ORDER = list(STEP_LABELS.keys())


# =============================================================================
# 图实例：跨 Streamlit rerun 复用同一 SqliteSaver，才能按 thread_id 断点续跑
# =============================================================================
@st.cache_resource(show_spinner=False)
def get_web_graph() -> CompiledReviewGraph:
    """编译与 CLI 相同的业务图，checkpoint 共用 checkpoints/threads.sqlite。

    interrupt_before=['human_review'] 只作用于本 Web 编译实例：
    图会在进入人工审核节点前暂停，从而避免调用节点内的终端 input()。
    确认/修改后通过 update_state(as_node='human_review') 写入与 CLI 相同的
    human_decision 字段，后续仍走 route_after_human_review 条件边。
    """

    checkpointer, _closer = _make_sqlite_checkpointer()
    return build_graph(
        checkpointer=checkpointer,
        interrupt_before=["human_review"],
    )


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _parse_stream_chunk(chunk: Any) -> tuple[str | None, Any]:
    """解析 graph.stream(stream_mode=['updates','values']) 的块格式（与 CLI 一致）。"""

    if isinstance(chunk, tuple) and len(chunk) == 2:
        return str(chunk[0]), chunk[1]
    if isinstance(chunk, dict) and "type" in chunk and "data" in chunk:
        return str(chunk.get("type")), chunk.get("data")
    return None, chunk


def _waiting_human(graph: CompiledReviewGraph, config: dict[str, Any]) -> bool:
    snapshot = graph.get_state(config)
    next_nodes = tuple(snapshot.next) if snapshot else ()
    return "human_review" in next_nodes


def _snapshot_values(graph: CompiledReviewGraph, config: dict[str, Any]) -> dict[str, Any]:
    snapshot = graph.get_state(config)
    values = snapshot.values if snapshot else {}
    return dict(values) if isinstance(values, dict) else {}


def _append_log(node_name: str, update: Any) -> None:
    label = STEP_LABELS.get(node_name, node_name)
    st.session_state.logs.append(
        {
            "node": node_name,
            "label": label,
            "preview": _preview(update, limit=280),
        }
    )


def _render_logs(placeholder: Any) -> None:
    """在占位符中重绘运行日志，实现循环内的实时刷新。"""

    logs: list[dict[str, str]] = st.session_state.logs
    if not logs:
        placeholder.info("等待开始。提交需求后将按 拆解 → 搜索 → 撰写 → 自检 → 人工审核 刷新。")
        return

    parts: list[str] = []
    for index, item in enumerate(logs, start=1):
        parts.append(
            f"**{index}. {item['label']}** `{item['node']}`\n\n"
            f"```json\n{item['preview']}\n```"
        )
    placeholder.markdown("\n\n".join(parts))


def _render_stepper(current: str | None, done: set[str]) -> None:
    """简洁商务风步骤条。"""

    cols = st.columns(len(STEP_ORDER))
    for col, node_name in zip(cols, STEP_ORDER, strict=True):
        label = STEP_LABELS[node_name]
        if node_name in done:
            mark, tone = "●", "已完成"
        elif node_name == current:
            mark, tone = "◎", "进行中"
        else:
            mark, tone = "○", "待执行"
        col.markdown(
            f"<div class='step-card'><div class='step-mark'>{mark}</div>"
            f"<div class='step-name'>{label}</div>"
            f"<div class='step-tone'>{tone}</div></div>",
            unsafe_allow_html=True,
        )


def run_until_pause_or_end(
    graph: CompiledReviewGraph,
    payload: AgentState | None,
    config: dict[str, Any],
    log_box: Any,
) -> str:
    """驱动 graph.stream，直到 HITL 断点或流程结束。返回 paused / done。"""

    done_nodes: set[str] = {item["node"] for item in st.session_state.logs}
    current: str | None = None

    try:
        stream = graph.stream(
            payload,
            config,
            stream_mode=["updates", "values"],
        )
        for chunk in stream:
            mode, data = _parse_stream_chunk(chunk)
            if mode == "updates" and isinstance(data, dict):
                for node_name, update in data.items():
                    current = str(node_name)
                    done_nodes.add(current)
                    _append_log(current, update)
                    _render_logs(log_box)
            elif mode == "values" and isinstance(data, dict):
                st.session_state.state_values = dict(data)
            elif isinstance(data, dict) and mode is None:
                for node_name, update in data.items():
                    current = str(node_name)
                    done_nodes.add(current)
                    _append_log(current, update)
                    _render_logs(log_box)
    except _collect_network_error_types() as exc:
        raise RuntimeError(_friendly_error(exc)) from exc

    st.session_state.state_values = _snapshot_values(graph, config)
    st.session_state.done_nodes = done_nodes
    st.session_state.current_node = "human_review" if _waiting_human(graph, config) else current

    if _waiting_human(graph, config):
        return "paused"

    return "done"


def finish_and_save(thread_id: str) -> None:
    """流程结束后复用 CLI 的 _save_report，写入 output/ 并准备下载。"""

    values = st.session_state.state_values
    report = str(values.get("report") or "").strip()
    st.session_state.report = report
    saved = None
    if values:
        saved = _save_report(values, thread_id)  # type: ignore[arg-type]
    st.session_state.saved_path = str(saved) if saved else None


def apply_human_decision(
    graph: CompiledReviewGraph,
    config: dict[str, Any],
    *,
    decision: str,
    report: str,
    log_box: Any,
) -> str:
    """把网页人工审核结果写回图状态，模拟 human_review 节点输出，再继续流转。"""

    update: dict[str, Any] = {"human_decision": decision}
    if decision == "revise":
        update["report"] = report

    graph.update_state(config, update, as_node="human_review")
    _append_log("human_review", update)
    _render_logs(log_box)
    return run_until_pause_or_end(graph, None, config, log_box)


def init_session() -> None:
    defaults: dict[str, Any] = {
        "logs": [],
        "status": "idle",  # idle | running | paused | done | error
        "report": "",
        "saved_path": None,
        "error": "",
        "state_values": {},
        "done_nodes": set(),
        "current_node": None,
        "active_thread": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def apply_pending_widget_values() -> None:
    """把待写入的控件值落到 session_state，必须在对应 widget 实例化之前调用。

    Streamlit 禁止在 `key=xxx` 的控件创建之后再改 `st.session_state.xxx`。
    """

    pending_thread = st.session_state.pop("pending_thread_input", None)
    if pending_thread is not None:
        st.session_state.thread_input = pending_thread
    pending_query = st.session_state.pop("pending_query_input", None)
    if pending_query is not None:
        st.session_state.query_input = pending_query


def on_new_thread_id() -> None:
    """按钮回调在下一轮脚本最前执行，此时控件尚未实例化。"""

    st.session_state.pending_thread_input = str(uuid.uuid4())[:8]


def on_fill_example_query() -> None:
    st.session_state.pending_query_input = EXAMPLE_QUERY


def inject_business_css() -> None:
    """企业内部使用的简洁商务样式：深色顶栏、克制配色、卡片步骤。"""

    st.markdown(
        """
        <style>
        .block-container { max-width: 1180px; padding-top: 1.4rem; }
        h1, h2, h3 { color: #1f2a44; letter-spacing: 0.02em; }
        .hero {
            background: linear-gradient(90deg, #1f2a44 0%, #2f4a6d 100%);
            color: #f5f7fa;
            padding: 18px 22px;
            border-radius: 8px;
            margin-bottom: 16px;
        }
        .hero small { opacity: 0.85; }
        .step-card {
            border: 1px solid #d9e0ea;
            background: #f8fafc;
            border-radius: 8px;
            padding: 10px 8px;
            text-align: center;
            min-height: 86px;
        }
        .step-mark { font-size: 16px; color: #2f4a6d; }
        .step-name { font-weight: 600; color: #1f2a44; font-size: 13px; margin-top: 4px; }
        .step-tone { color: #6b7c93; font-size: 12px; margin-top: 2px; }
        .hitl-panel {
            border: 1px solid #c7a24a;
            background: #fffbeb;
            padding: 16px 18px;
            border-radius: 8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    st.markdown(
        """
        <div class="hero">
            <div style="font-size:22px;font-weight:700;">业务报告撰写与审核Agent</div>
            <small>LangGraph 工作流可视化 · 任务拆解 / Tavily 检索 / 自检返工 / 人工审核 / 断点续跑</small>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar() -> None:
    missing = _validate_settings()
    st.sidebar.markdown("### 运行环境")
    if missing:
        st.sidebar.error("缺少密钥：" + "、".join(missing) + "。请检查项目根目录 `.env`。")
    else:
        st.sidebar.success("DeepSeek / Tavily 配置已加载")
    st.sidebar.caption(f"Checkpoint：`{CHECKPOINT_DB}`")
    st.sidebar.caption("报告目录：`output/`")
    st.sidebar.markdown("### 示例需求")
    st.sidebar.code(EXAMPLE_QUERY, language=None)
    st.sidebar.button("填入示例需求", on_click=on_fill_example_query)


def main() -> None:
    st.set_page_config(
        page_title="业务报告撰写与审核Agent",
        page_icon="📋",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    init_session()
    apply_pending_widget_values()
    inject_business_css()
    render_header()
    render_sidebar()

    graph = get_web_graph()
    log_box = None  # 在日志区创建占位符

    # ----- 输入区 -----
    st.subheader("1. 任务输入")
    query = st.text_area(
        "业务复盘 / 调研需求",
        key="query_input",
        height=110,
        placeholder="例如：调研2026大模型Agent企业落地方案",
    )
    col_t, col_new, col_run = st.columns([3, 1, 1])
    with col_t:
        thread_id = st.text_input(
            "会话 Thread ID（可填历史 ID 断点续跑；留空则自动新建）",
            key="thread_input",
            placeholder="例如 demo-1",
        ).strip()
    with col_new:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        st.button(
            "新建会话 ID",
            use_container_width=True,
            on_click=on_new_thread_id,
        )
    with col_run:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        start_clicked = st.button("开始 / 续跑", type="primary", use_container_width=True)

    # ----- 步骤条 -----
    st.subheader("2. 流程进度")
    current = st.session_state.current_node
    if st.session_state.status == "paused":
        current = "human_review"
    _render_stepper(current, set(st.session_state.done_nodes))

    # ----- 日志 -----
    st.subheader("3. 运行日志")
    log_box = st.empty()
    _render_logs(log_box)

    # ----- 启动 / 续跑 -----
    if start_clicked:
        missing = _validate_settings()
        if missing:
            st.session_state.status = "error"
            st.session_state.error = "密钥未配置：" + "、".join(missing)
        elif not query.strip() and not thread_id:
            st.session_state.status = "error"
            st.session_state.error = "请输入业务需求，或填写已有 Thread ID 以恢复会话。"
        else:
            if not thread_id:
                thread_id = str(uuid.uuid4())[:8]
                # 本轮控件已实例化，不能再写 thread_input；下一轮 rerun 前再回填
                st.session_state.pending_thread_input = thread_id

            st.session_state.active_thread = thread_id
            st.session_state.logs = []
            st.session_state.error = ""
            st.session_state.report = ""
            st.session_state.saved_path = None
            st.session_state.done_nodes = set()
            st.session_state.current_node = "decompose"
            st.session_state.status = "running"

            config = _config(thread_id)
            try:
                payload, mode = _resolve_payload(graph, config, query.strip())
                # 已停在人工审核节点：不要 stream(None)，否则会进入节点内的 input()
                if _waiting_human(graph, config):
                    st.session_state.state_values = _snapshot_values(graph, config)
                    st.session_state.status = "paused"
                    st.session_state.current_node = "human_review"
                    st.session_state.done_nodes = {
                        "decompose",
                        "search",
                        "write_report",
                        "review",
                    }
                elif mode == "finished":
                    values = _snapshot_values(graph, config)
                    st.session_state.state_values = values
                    st.session_state.status = "done"
                    finish_and_save(thread_id)
                else:
                    with st.spinner(
                        "正在执行工作流（拆解 / 搜索 / 撰写 / 自检）…"
                    ):
                        result = run_until_pause_or_end(graph, payload, config, log_box)
                    st.session_state.status = result
                    if result == "done":
                        finish_and_save(thread_id)
            except Exception as exc:
                st.session_state.status = "error"
                st.session_state.error = _friendly_error(exc)
            st.rerun()

    if st.session_state.status == "error" and st.session_state.error:
        st.error(st.session_state.error)

    thread_id = st.session_state.active_thread or thread_id
    config = _config(thread_id) if thread_id else None

    # ----- 人工审核「弹窗」面板（暂停时展示） -----
    if st.session_state.status == "paused" and config is not None:
        values = st.session_state.state_values or _snapshot_values(graph, config)
        st.markdown("### 4. 人工审核")
        st.markdown(
            "<div class='hitl-panel'><b>流程已暂停，等待人工确认。</b>"
            " 可直接通过，或修改报告后重新进入自检。</div>",
            unsafe_allow_html=True,
        )
        comments = values.get("review_comments") or "（无）"
        st.caption("自检意见")
        st.info(comments)

        with st.form("hitl_form", clear_on_submit=False):
            edited_report = st.text_area(
                "报告正文（可在线修改）",
                value=str(values.get("report") or ""),
                height=360,
            )
            left, right = st.columns(2)
            with left:
                approved = st.form_submit_button("确认通过", type="primary", use_container_width=True)
            with right:
                revised = st.form_submit_button("提交修改并重新自检", use_container_width=True)

        if approved or revised:
            if not edited_report.strip():
                st.warning("报告正文不能为空。")
            else:
                decision = "revise" if revised else "approve"
                try:
                    with st.spinner("正在提交人工审核结果并继续流转…"):
                        result = apply_human_decision(
                            graph,
                            config,
                            decision=decision,
                            report=edited_report.strip(),
                            log_box=log_box,
                        )
                    st.session_state.status = result
                    if result == "done":
                        finish_and_save(thread_id)
                except Exception as exc:
                    st.session_state.status = "error"
                    st.session_state.error = _friendly_error(exc)
                st.rerun()

    # ----- 最终报告 -----
    if st.session_state.status == "done":
        st.markdown("### 4. 最终报告")
        report = st.session_state.report or str(
            st.session_state.state_values.get("report") or ""
        )
        if not report:
            st.warning("最终 state 中没有报告内容。")
        else:
            if st.session_state.saved_path:
                st.success(f"已保存到本地：{st.session_state.saved_path}")
            st.download_button(
                "一键下载 Markdown 报告",
                data=report.encode("utf-8"),
                file_name=f"report_{thread_id or 'latest'}.md",
                mime="text/markdown",
                type="primary",
            )
            st.markdown(report)

    if thread_id:
        st.caption(f"当前会话 thread_id = `{thread_id}`")


if __name__ == "__main__":
    main()
