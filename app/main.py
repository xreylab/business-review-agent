"""业务复盘 Agent 命令行入口。

测试示例 query（交互模式直接回车即可使用）：
    调研2026大模型Agent企业落地方案

用法：
    python -m app.main
    python -m app.main --query "调研2026大模型Agent企业落地方案"
    python -m app.main --query "调研2026大模型Agent企业落地方案" --thread-id demo-1
    python -m app.main --thread-id demo-1
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# 允许 `python app/main.py` 从项目根目录启动
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.config import settings
from app.graph.builder import CompiledReviewGraph, build_graph
from app.graph.state import AgentState

# 测试示例：调研2026大模型Agent企业落地方案
EXAMPLE_QUERY = "调研2026大模型Agent企业落地方案"

OUTPUT_DIR = _PROJECT_ROOT / "output"
CHECKPOINT_DIR = _PROJECT_ROOT / "checkpoints"
CHECKPOINT_DB = CHECKPOINT_DIR / "threads.sqlite"

# ANSI 颜色（Windows 10+ 终端可用）
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"
_WHITE = "\033[37m"

_NODE_COLORS: dict[str, str] = {
    "decompose": _CYAN,
    "search": _BLUE,
    "write_report": _GREEN,
    "review": _YELLOW,
    "human_review": _MAGENTA,
}


def _c(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}"


def _enable_windows_ansi() -> None:
    """打开 Windows 控制台的 VT 颜色支持。"""

    if sys.platform != "win32":
        return
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _preview(data: Any, *, limit: int = 360) -> str:
    """把 state / update 压成可读 JSON，长文本截断以免刷屏。"""

    def shorten(value: Any) -> Any:
        if isinstance(value, str) and len(value) > limit:
            return f"{value[:limit]}...（截断 {len(value) - limit} 字）"
        if isinstance(value, list):
            return [shorten(item) for item in value]
        if isinstance(value, dict):
            return {str(key): shorten(item) for key, item in value.items()}
        return value

    return json.dumps(shorten(data), ensure_ascii=False, indent=2, default=str)


def _initial_state(query: str) -> AgentState:
    return {
        "user_query": query,
        "task_list": [],
        "search_results": [],
        "report": "",
        "review_comments": "",
        "retry_count": 0,
        "human_decision": "",
    }


def _validate_settings() -> list[str]:
    missing: list[str] = []
    if not settings.deepseek_api_key:
        missing.append("DEEPSEEK_API_KEY")
    if not settings.tavily_api_key:
        missing.append("TAVILY_API_KEY")
    return missing


def _make_sqlite_checkpointer() -> tuple[Any, Any]:
    """返回 (checkpointer, closer)。closer() 用于释放连接。"""

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError(
            "未安装 langgraph-checkpoint-sqlite，无法跨进程恢复 thread_id。"
            "请执行: pip install langgraph-checkpoint-sqlite"
        ) from exc

    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    if hasattr(checkpointer, "setup"):
        checkpointer.setup()
    return checkpointer, conn.close


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="业务复盘 Agent CLI（LangGraph V1）",
    )
    parser.add_argument(
        "-q",
        "--query",
        default="",
        help="业务调研 / 复盘需求。省略则进入交互输入。",
    )
    parser.add_argument(
        "-t",
        "--thread-id",
        default="",
        help="会话 thread_id。传入已有 id 可恢复断点；省略则自动生成。",
    )
    return parser.parse_args()


def _read_query_and_thread(args: argparse.Namespace) -> tuple[str, str]:
    query = (args.query or "").strip()
    thread_id = (args.thread_id or "").strip()

    if not query and not thread_id:
        print(_c("业务复盘 Agent", _BOLD + _CYAN))
        print(_c(f"示例 query：{EXAMPLE_QUERY}", _DIM))
        typed = input("请输入业务需求（直接回车使用示例）：").strip()
        query = typed or EXAMPLE_QUERY
        typed_thread = input("thread_id（直接回车自动生成，填入已有 id 可恢复会话）：").strip()
        thread_id = typed_thread or str(uuid.uuid4())[:8]
        return query, thread_id

    if not thread_id:
        thread_id = str(uuid.uuid4())[:8]
    return query, thread_id


def _resolve_payload(
    graph: CompiledReviewGraph,
    config: dict[str, Any],
    query: str,
) -> tuple[AgentState | None, str]:
    """决定本次 stream 的输入：新任务 or 断点恢复。"""

    snapshot = graph.get_state(config)
    values = snapshot.values if snapshot else None
    pending = tuple(snapshot.next) if snapshot else ()

    if pending and values:
        print(_c(f"[恢复] thread 未结束，将从节点 {list(pending)} 续跑。", _YELLOW))
        if query:
            print(_c("已忽略本次 --query，继续使用会话中的原需求。", _DIM))
        return None, "resume"

    if values and not pending and not query:
        print(_c("[提示] 该 thread 已跑完。未提供新 query，将展示上次结果。", _YELLOW))
        return None, "finished"

    if not query:
        raise ValueError("未找到可恢复的断点，请同时提供 --query 以开始新任务。")

    return _initial_state(query), "start"


def _print_node_update(node_name: str, update: Any) -> None:
    color = _NODE_COLORS.get(node_name, _WHITE)
    print()
    print(_c("─" * 60, _DIM))
    print(_c(f"▶ 节点完成：{node_name}", _BOLD + color))
    print(_c("state 更新：", _DIM))
    print(_preview(update))


def _print_state_snapshot(state: Any) -> None:
    if not isinstance(state, dict):
        return
    compact = {
        "user_query": state.get("user_query"),
        "task_list": state.get("task_list"),
        "search_results_count": len(state.get("search_results") or []),
        "retry_count": state.get("retry_count"),
        "human_decision": state.get("human_decision"),
        "review_comments": state.get("review_comments"),
        "report": state.get("report"),
    }
    print(_c("当前 state 快照：", _DIM))
    print(_preview(compact))


def _consume_stream(
    graph: CompiledReviewGraph,
    payload: AgentState | None,
    config: dict[str, Any],
) -> AgentState | None:
    """遍历 graph.stream，打印彩色节点日志与 state 变化，返回最终 state。"""

    final_state: AgentState | None = None
    stream = graph.stream(
        payload,
        config,
        stream_mode=["updates", "values"],
    )

    for chunk in stream:
        mode: str | None = None
        data: Any = chunk

        if isinstance(chunk, tuple) and len(chunk) == 2:
            mode, data = chunk[0], chunk[1]
        elif isinstance(chunk, dict) and "type" in chunk and "data" in chunk:
            mode, data = str(chunk.get("type")), chunk.get("data")

        if mode == "updates" and isinstance(data, dict):
            for node_name, update in data.items():
                _print_node_update(str(node_name), update)
        elif mode == "values":
            final_state = data  # type: ignore[assignment]
            _print_state_snapshot(data)
        elif isinstance(data, dict) and mode is None:
            # 兼容只返回 updates dict 的旧行为
            for node_name, update in data.items():
                _print_node_update(str(node_name), update)

    snapshot = graph.get_state(config)
    if snapshot and snapshot.values:
        final_state = snapshot.values  # type: ignore[assignment]
    return final_state


def _save_report(state: AgentState, thread_id: str) -> Path | None:
    """把最终报告写成 markdown，输出到 output/。"""

    report = (state.get("report") or "").strip()
    if not report:
        print(_c("[提示] 最终 state 中没有报告，跳过保存。", _YELLOW))
        return None

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_thread = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in thread_id)
    path = OUTPUT_DIR / f"report_{safe_thread}_{stamp}.md"

    header = [
        f"> thread_id: `{thread_id}`",
        f"> 生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"> 原始需求: {state.get('user_query') or ''}",
        f"> 重试次数: {state.get('retry_count', 0)}",
        "",
    ]
    body = report if report.lstrip().startswith("#") else f"# 业务复盘报告\n\n{report}"
    path.write_text("\n".join(header) + body + "\n", encoding="utf-8")
    return path


def _friendly_error(exc: BaseException) -> str:
    name = type(exc).__name__
    text = str(exc) or name
    lowered = f"{name} {text}".lower()

    if isinstance(exc, KeyboardInterrupt):
        return "已手动中断。可用同一 --thread-id 稍后恢复未完成的会话。"
    if isinstance(exc, ValueError):
        return text
    if "authentication" in lowered or "api key" in lowered or "401" in lowered:
        return f"API 鉴权失败，请检查 .env 中的 DEEPSEEK_API_KEY / TAVILY_API_KEY。详情：{text}"
    if "rate limit" in lowered or "429" in lowered:
        return f"接口触发限流，请稍后重试。详情：{text}"
    if any(token in lowered for token in ("timeout", "timed out")):
        return f"请求超时，请检查网络后重试。详情：{text}"
    if any(
        token in lowered
        for token in ("connection", "connect", "network", "dns", "unreachable")
    ):
        return f"网络连接失败，请检查网络或代理。详情：{text}"
    if "api" in lowered or name.endswith("Error"):
        return f"调用外部服务出错（{name}）：{text}"
    return f"运行失败（{name}）：{text}"


def _collect_network_error_types() -> tuple[type[BaseException], ...]:
    extra: list[type[BaseException]] = [
        ConnectionError,
        TimeoutError,
    ]
    try:
        from openai import (
            APIConnectionError,
            APIError,
            APITimeoutError,
            AuthenticationError,
            RateLimitError,
        )

        extra.extend(
            [
                APIError,
                APIConnectionError,
                APITimeoutError,
                AuthenticationError,
                RateLimitError,
            ]
        )
    except Exception:
        pass
    try:
        from httpx import ConnectError, HTTPStatusError, TimeoutException

        extra.extend([ConnectError, TimeoutException, HTTPStatusError])
    except Exception:
        pass
    return tuple(extra)


def main() -> int:
    _enable_windows_ansi()
    args = _parse_args()
    closer = None

    try:
        missing = _validate_settings()
        if missing:
            print(
                _c(
                    "配置不完整，请复制 .env.example 为 .env 并填写："
                    + ", ".join(missing),
                    _RED,
                )
            )
            return 1

        query, thread_id = _read_query_and_thread(args)
        checkpointer, closer = _make_sqlite_checkpointer()
        graph = build_graph(checkpointer=checkpointer)
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

        print()
        print(_c(f"thread_id = {thread_id}", _BOLD + _WHITE))
        print(_c(f"checkpoint = {CHECKPOINT_DB}", _DIM))
        if query:
            print(_c(f"query = {query}", _WHITE))

        payload, mode = _resolve_payload(graph, config, query)
        if mode == "finished":
            snapshot = graph.get_state(config)
            final_state = snapshot.values if snapshot else None
            if not final_state:
                print(_c("该 thread 没有可展示的 state。", _YELLOW))
                return 0
            saved = _save_report(final_state, thread_id)  # type: ignore[arg-type]
            if saved:
                print(_c(f"已写出上次报告：{saved}", _GREEN))
            return 0

        print(_c(f"模式 = {'断点恢复' if mode == 'resume' else '新任务'}", _CYAN))
        print(_c("开始执行图…", _DIM))

        final_state = _consume_stream(graph, payload, config)
        print()
        print(_c("═" * 60, _DIM))
        print(_c("流程结束", _BOLD + _GREEN))

        if not final_state:
            print(_c("未拿到最终 state。", _YELLOW))
            return 1

        saved = _save_report(final_state, thread_id)
        if saved:
            print(_c(f"报告已保存：{saved}", _BOLD + _GREEN))
        return 0

    except KeyboardInterrupt:
        print()
        print(_c(_friendly_error(KeyboardInterrupt()), _YELLOW))
        return 130
    except _collect_network_error_types() as exc:
        print()
        print(_c(_friendly_error(exc), _RED))
        return 2
    except Exception as exc:
        print()
        print(_c(_friendly_error(exc), _RED))
        return 1
    finally:
        if closer is not None:
            try:
                closer()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
