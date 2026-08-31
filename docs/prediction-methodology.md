# 预测方法说明

## 默认四维框架

| 维度 | 默认权重 | 主要输入 |
|---|---:|---|
| 大盘环境 | 30% | 上证指数、沪深 300、创业板指、成交额、市场宽度 |
| 资金行为 | 30% | 主力资金、融资融券、龙虎榜、成交量变化 |
| 技术面 | 25% | 均线、MACD、RSI、布林带、支撑阻力、波动率 |
| 消息面 | 15% | 公告、财报、行业政策、新闻、监管披露 |

## 预测输出

每份预测至少包含：

- 看多、看空或震荡；
- 概率估计；
- 预期价格区间；
- 支撑位和压力位；
- 触发条件；
- 失效条件；
- 风险提示；
- 数据截止时间和来源。

## 预测输入质量门禁

行情必须先经过 `src.analysis.quality_gate.evaluate_prediction_input`：

- `approved`：主源质量检查和主备交叉验证正常；
- `degraded`：主源可用，但存在备用源不可用、交叉验证跳过或普通质量警告；
- `blocked`：数据为空、质量错误、交易日历异常或主备行情不一致，不得计算预测。

门禁同时记录行情数据截止日、质量报告状态、交易日历状态和交叉验证状态。预测入口应使用 `require_prediction_input`，避免绕过门禁。

## 技术指标计算层

通过 `src.analysis.calculate_technical_indicators` 计算技术面特征。该入口会先执行预测输入质量门禁，只有 `approved` 或 `degraded` 数据可以计算，`blocked` 数据会抛出异常。

当前输出包括：

- `ma_5`、`ma_10`、`ma_20`、`ma_60`：简单移动平均线；
- `macd_dif`、`macd_dea`、`macd_hist`：12/26/9 MACD；
- `rsi_6`、`rsi_14`：相对强弱指标；
- `bollinger_mid_20`、`bollinger_upper_20`、`bollinger_lower_20`、`bollinger_bandwidth_20`、`bollinger_percent_b_20`：布林带；
- `true_range`、`atr_14`：真实波幅和平均真实波幅；
- `support_20`、`resistance_20`、`support_60`、`resistance_60`：滚动支撑/压力位；
- `return_1d`、`volatility_20`：收益率和波动率。

计算前会按交易日期升序排列，滚动窗口和指数平均只使用当前行及之前的数据，避免未来函数。历史记录不足时保留 `NaN`，并在摘要中记录告警，不用 0 填充。每日流水线默认使用目标交易日前 120 个自然日的历史行情，先通过质量门禁，再保存带指标数据到 `data/processed/market/indicators/`，并在 Markdown 报告中展示最新指标和指标告警。

## 防止未来函数

财报、公告、新闻和资金数据必须按公开披露时间进入模型。回测或历史复盘时，不能使用预测时点之后才公开的数据。
