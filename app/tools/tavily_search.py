"""Tavily 搜索工具封装：按查询返回网页摘要与来源链接。"""

from langchain_tavily import TavilySearch as TavilySearchResults

from app.config import settings

# 单次检索最多返回条数
MAX_SEARCH_RESULTS = 3


def get_tavily_search_tool() -> TavilySearchResults:
    """创建 TavilySearchResults 工具实例，单次最多返回 3 条结果。

    langchain-tavily 新版本将原 TavilySearchResults 重命名为 TavilySearch，
    此处用别名保持课程/简历项目中的类名习惯；密钥从 config 读取。
    """

    tool_kwargs: dict = {
        "max_results": MAX_SEARCH_RESULTS,
        "topic": "general",
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
    }
    # 有配置则显式传入；否则回退到环境变量 TAVILY_API_KEY
    if settings.tavily_api_key:
        tool_kwargs["tavily_api_key"] = settings.tavily_api_key

    return TavilySearchResults(**tool_kwargs)


# 默认搜索工具单例，节点侧可直接 from app.tools.tavily_search import tavily_search
tavily_search = get_tavily_search_tool()
