"""行情数据的内部字段定义。"""

from __future__ import annotations

STANDARD_COLUMNS = [
    "symbol",
    "exchange",
    "asset_type",
    "trade_date",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "adjust_factor",
    "currency",
    "source",
    "source_record_id",
    "retrieved_at",
    "data_version",
    "frequency",
    "price_adjustment",
]

REQUIRED_COLUMNS = [
    "symbol",
    "exchange",
    "asset_type",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "frequency",
    "price_adjustment",
    "source",
    "retrieved_at",
]

OPTIONAL_COLUMNS = [
    "volume",
    "amount",
    "adjust_factor",
    "currency",
    "timestamp",
    "source_record_id",
    "data_version",
]

# 中英文供应商字段统一映射到内部字段。保留未映射字段，并由 normalizer 加上 extra_ 前缀。
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "ts_code", "代码", "股票代码", "证券代码", "品种代码", "ticker"),
    "exchange": ("exchange", "交易所", "市场"),
    "asset_type": ("asset_type", "资产类型", "证券类型"),
    "trade_date": ("trade_date", "日期", "交易日期", "交易日", "date"),
    "timestamp": ("timestamp", "时间戳", "行情时间", "datetime", "date_time", "时间"),
    "open": ("open", "开盘", "开盘价"),
    "high": ("high", "最高", "最高价"),
    "low": ("low", "最低", "最低价"),
    "close": ("close", "收盘", "收盘价", "最新价"),
    "volume": ("volume", "vol", "成交量", "成交股数", "总手"),
    "amount": ("amount", "成交额", "成交金额"),
    "adjust_factor": ("adjust_factor", "adj_factor", "复权因子"),
    "currency": ("currency", "币种", "货币"),
    "source_record_id": ("source_record_id", "record_id", "id", "trade_id"),
    "data_version": ("data_version", "数据版本", "版本"),
    "frequency": ("frequency", "频率", "周期"),
    "price_adjustment": ("price_adjustment", "复权", "复权类型", "adjust"),
}

QUALITY_RULES_VERSION = "1.0"
DUPLICATE_KEY_COLUMNS = ("symbol", "trade_date", "frequency", "price_adjustment", "source")
