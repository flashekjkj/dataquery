"""HTTP、CLI与独立MCP入口使用同一服务端数据源配置。"""
import json
from pathlib import Path
from app.config import settings
from datasources.models import DataSourceConfig

BASE = Path(__file__).resolve().parents[1]


def load_source_config():
    if not settings.DATA_SOURCE_FILE:
        return DataSourceConfig(source_id='demo',dialect='duckdb',
            path=str(BASE/'data/demo_ecom.duckdb'),demo_catalog=True)
    path = Path(settings.DATA_SOURCE_FILE)
    if not path.is_absolute():
        path = BASE / path
    config = DataSourceConfig.model_validate(json.loads(path.read_text(encoding='utf-8')))
    if config.path and not Path(config.path).is_absolute():
        config = config.model_copy(update={'path': str(path.parent / config.path)})
    updates={}
    if config.policy_file and not Path(config.policy_file).is_absolute():
        updates['policy_file']=str(path.parent/config.policy_file)
    updates['semantic_files']=tuple(str(path.parent/p) if not Path(p).is_absolute() else p for p in config.semantic_files)
    return config.model_copy(update=updates)
