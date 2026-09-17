"""DeepSeek 客户端（OpenAI 兼容 ChatModel），供各节点复用。"""

from langchain_openai import ChatOpenAI

from app.config import settings


def get_deepseek_llm(*, temperature: float = 0.2) -> ChatOpenAI:
    """创建 DeepSeek ChatOpenAI 兼容实例。

    DeepSeek 开放平台走 OpenAI 协议，因此直接使用 ChatOpenAI，
    并从 config 读取 api_key / base_url / model_name（对应 deepseek_model）。
    """

    return ChatOpenAI(
        api_key=settings.deepseek_api_key or None,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,  # config 中的 model_name
        temperature=temperature,
        max_retries=2,
        timeout=60,
    )


# 默认 LLM 单例，节点侧可直接 from app.llm.deepseek import llm
llm = get_deepseek_llm()
