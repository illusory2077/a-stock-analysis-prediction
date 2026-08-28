# 统一数据字段

## 行情表 `market_bars`

```text
symbol             标的代码，例如 600519.SH 或 AAPL
exchange           交易所，例如 SSE、SZSE、NYSE、NASDAQ
asset_type         stock / index / etf / fx / crypto
trade_date         交易所本地交易日
timestamp          UTC 时间戳
open               开盘价
high               最高价
low                最低价
close              收盘价或最新价
volume             成交量
amount             成交额
adjust_factor      复权因子（如适用）
currency           CNY / USD / HKD 等
frequency          tick / 1m / 5m / 1d 等
price_adjustment   none / qfq / hfq
source             数据供应商
source_record_id   原始记录标识
retrieved_at       实际获取时间
is_delayed         是否延迟数据
data_status        raw / normalized / validated / rejected
```

## 事件表 `market_events`

```text
symbol
exchange
event_time
published_at
event_type
title
content
source
url
importance
retrieved_at
```

## 新闻表 `news_items`

```text
news_id
query
title
url
canonical_url
source
published_at
retrieved_at
content
summary
score
related_symbols
source_provider
source_record_id
data_status
```

- `published_at` 是新闻公开时间，`retrieved_at` 是本项目实际检索时间。
- `canonical_url` 去除常见跟踪参数后用于去重。
- Tavily 原始响应必须另存于 `data/raw/news/`，标准化记录保存于 `data/processed/news/`。

## 财务表 `fundamentals`

```text
symbol
report_period
published_at
metric
value
unit
currency
source
retrieved_at
```

## 关键约定

- 所有时间都必须可追溯到交易所本地时间或 UTC。
- `published_at` 优先于 `report_period` 决定数据何时可进入模型。
- 不同来源的价格若复权口径不同，不得直接比较。
- 原始数据与标准化数据分开保存。
