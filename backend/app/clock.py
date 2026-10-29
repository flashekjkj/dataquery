"""可注入业务时钟：评测固定时间，正常服务使用上海时间。"""
from contextvars import ContextVar
from datetime import datetime
from zoneinfo import ZoneInfo

evaluation_now = ContextVar("evaluation_now", default=None)


def business_now():
    return evaluation_now.get() or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
