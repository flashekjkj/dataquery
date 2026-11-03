from datasources.base import BaseConnector
from datasources.models import DataSourceConfig, QueryContext


class DataSourceRegistry:
    """服务端注册数据源；凭据仅存在Connector配置中。"""
    def __init__(self):
        self._connectors: dict[str, BaseConnector] = {}

    def register(self, config: DataSourceConfig):
        if config.source_id in self._connectors:
            raise ValueError('重复source_id')
        if config.dialect == 'duckdb':
            from datasources.duckdb import DuckDBConnector
            connector = DuckDBConnector(config)
        else:
            from datasources.postgres import PostgresConnector
            connector = PostgresConnector(config)
        from datasources.secured import SecuredConnector
        try:
            secured=SecuredConnector(connector)
        except Exception:
            connector.close()
            raise
        self._connectors[config.source_id] = secured
        return secured

    def get(self, source_id):
        return self._connectors[source_id]

    def context(self, source_id):
        return self.get(source_id).context.model_copy(deep=True)

    def refresh(self, source_id):
        return self.get(source_id).refresh().model_copy(deep=True)

    def close(self):
        for connector in self._connectors.values():
            connector.close()
        self._connectors.clear()
