# 数据源登记表

## 已配置数据源

| 数据源 | 环境变量 | 数据类型 | 实时能力 | 状态 |
|---|---|---|---|---|
| Tushare Pro | `TUSHARE_TOKEN` | A 股行情、公告、财报、资金、龙虎榜 | 实时日线/分钟能力取决于权限 | 已配置 |
| AKShare | 无 | A 股补充行情、研究型实时快照 | 依赖公开接口，稳定性有限 | 无 Key 即可使用 |
| FRED | `FRED_API_KEY` | 美国及全球宏观数据 | 非盘中行情流 | 已配置 |
| Alpha Vantage | `ALPHAVANTAGE_KEY` | 全球低频行情、外汇、技术指标和金融新闻 | 实时能力取决于套餐 | 已配置 |
| Polygon | `POLYGON_API_KEY` | 美股历史、实时和 WebSocket | 可实时，取决于市场数据权限 | 可选 |
| SEC EDGAR | `SEC_USER_AGENT` | 美股财报、监管文件、公司披露 | 按披露更新，不是行情流 | 已配置 User-Agent |
| Tavily | `TAVILY_API_KEY` | 新闻搜索、网页正文、事件发现 | 取决于套餐和搜索接口 | 已配置 |

## 数据源使用原则

1. Tushare 是 A 股主数据源，AKShare 用于补充和交叉验证。
2. Alpha Vantage 主要用于全球低频行情和金融新闻；需要美股实时行情时优先评估 Polygon。
3. 公告/财报当前使用 Tushare `anns_d` 和 `income`；财报按 `f_ann_date`/`ann_date` 的实际公开披露时间过滤，AKShare 尚未接入统一适配器。
4. Tavily 用于新闻搜索和网页研究，不替代交易所公告、Tushare 或 SEC 等权威数据源。
5. FRED 和 SEC EDGAR 不提供股票盘口实时行情，不能替代行情供应商。
6. 生产环境不得把网页抓取接口当作唯一实时数据源。
7. 每次采集必须记录 `source`、`retrieved_at` 和数据延迟状态。
8. 任何数据源的商业使用和再分发都必须单独核对授权条款。


## 路由和降级

项目通过 `src/data_providers/router.py` 按优先级调用数据源：

- A 股日线：Tushare Pro → AKShare；
- 新闻搜索：Tavily；
- 路由器会记录每次尝试、失败原因、最终来源和是否发生降级；
- 网络错误、连接中断、HTTP 429/5xx 和明显限流错误会进入退避重试；
- 生产报告必须保留降级信息，不得把备用源结果伪装成主源结果。

`src/utils/http_client.py` 为 FRED、SEC 等 REST 数据源提供统一超时、重试、退避和最小请求间隔。

## 公告与财报落盘

- 原始接口响应：`data/raw/disclosures/{source}/{retrieved_date}/`；
- 标准化 JSONL、异常记录、质量报告和元数据：`data/processed/disclosures/`；
- 缺少或无法解析公开披露时间，以及晚于预测信息截面的记录，会保留审计但不进入消息面评分；
- 公告/财报获取失败不会伪造数据，也不阻断行情、技术面和资金面分析，失败原因会写入每日报告。
