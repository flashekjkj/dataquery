from pydantic_settings import BaseSettings,SettingsConfigDict
from pathlib import Path

# 获取backend目录
BASE_DIR = Path(__file__).parent.parent
ENV_FILE_PATH = BASE_DIR / ".env"

class Settings(BaseSettings):
    """LLL相关配置"""
    LLM_BASE_URL:str
    LLM_API_KEY:str
    LLM_MODEL_ID:str
    LLM_TIMEOUT:int
    LLM_EMBEDDING_MODEL:str

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8"
    )

settings = Settings()
