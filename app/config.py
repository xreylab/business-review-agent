"""从环境变量加载配置（DeepSeek / Tavily / LangSmith）。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    tavily_api_key: str = ""

    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "business-review-agent"


settings = Settings()
