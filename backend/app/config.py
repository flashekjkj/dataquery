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
    # 本地rerank模型（可选，默认bge-reranker-base，需已下载到本地HF缓存）
    RERANK_MODEL_ID:str = "BAAI/bge-reranker-base"

    # ===== LLM 失败治理 =====
    # 单次 LLM 调用的最大尝试次数（含首次，默认 3 次 = 最多重试 2 次）
    LLM_MAX_RETRIES:int = 3
    # 指数退避基数（秒）：等待时间在 [0, base*2^attempt] 内随机
    LLM_RETRY_BASE_DELAY:float = 1.0
    # 单次等待上限（秒），防止退避失控
    LLM_RETRY_MAX_DELAY:float = 30.0
    # SQL 生成/修复是否启用 JSON 结构化输出（response_format=json_object），
    # 服务端不支持时 ModelClient 自动降级为普通文本模式
    LLM_JSON_MODE:bool = True
    LLM_TEMPERATURE:float = 0.0

    # ===== 可观测性 =====
    # 日志级别：DEBUG / INFO / WARNING / ERROR
    OBS_LOG_LEVEL:str = "INFO"
    # 请求级 trace 是否落盘（data/traces/*.jsonl）
    OBS_TRACE_ENABLED:bool = True
    # trace 落盘目录（相对路径基于 backend/，绝对路径直接使用）
    OBS_TRACE_DIR:str = "data/traces"
    # /metrics 端点开关
    METRICS_ENABLED:bool = True

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8"
    )

settings = Settings()
