"""
结构化日志初始化（loguru）。

两个 sink：
1. 控制台：人读格式，自动附带 request_id / session_id / elapsed
2. 文件：JSON Lines（serialize=True），滚动保留，供日志采集器消费
   （data/logs/app.jsonl，默认 10MB × 7 天）

request_id 等上下文通过 patcher 注入：在 record 生成时读取 contextvars，
天然线程/协程安全，业务代码无需手动 bind。
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from observability.context import get_request_id, get_session_id

# 落盘目录：backend/data/logs（与会话/技能数据同根目录）
LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"

_initialized = False

# 控制台格式（支持 {extra} 占位）
_CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
    "<cyan>rid={extra[request_id]}</cyan> <dim>sid={extra[session_id]}</dim> | "
    "<cyan>{name}</cyan>:{function}:{line} - <level>{message}</level>"
)


def _patch_record(record: dict) -> None:
    """loguru patcher：每条日志生成时从 contextvars 注入请求上下文"""
    record["extra"]["request_id"] = get_request_id()
    record["extra"]["session_id"] = get_session_id()


def setup_logging(level: str = "INFO") -> None:
    """初始化日志（幂等：重复调用不叠加 sink）"""
    global _initialized
    if _initialized:
        return
    logger.remove()   # 清掉 loguru 默认 stderr handler，避免重复输出
    # 控制台：可读格式
    logger.add(
        sys.stderr, format=_CONSOLE_FORMAT, level=level,
        colorize=None, backtrace=True, diagnose=False,
    )
    # 文件：JSON Lines，供 grep/jq 或日志采集器消费
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(
        LOG_DIR / "app.jsonl",
        level=level, serialize=True, encoding="utf-8",
        rotation="10 MB", retention="7 days", enqueue=True,   # enqueue：写盘不阻塞业务
    )
    logger.configure(patcher=_patch_record)
    _initialized = True
    logger.info("日志系统初始化完成，JSON 日志目录：{}", LOG_DIR)
