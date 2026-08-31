# A股与全球市场分析预测项目

这是一个用于行情采集、数据标准化、市场分析和 A 股个股次日走势预测的项目骨架。

## 快速开始

### 1. 创建 Python 虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 填写本地密钥

编辑项目根目录的 `.env`：

```env
TUSHARE_TOKEN=你的Tushare Token
FRED_API_KEY=你的FRED API Key
ALPHAVANTAGE_KEY=你的Alpha Vantage Key
TAVILY_API_KEY=你的Tavily API Key
POLYGON_API_KEY=你的Polygon Key
SEC_USER_AGENT=AStockAnalysis your_email@example.com

# 行情质量校验
MARKET_CROSS_VALIDATE=true
MARKET_VALIDATE_CALENDAR=true
MARKET_CLOSE_DIFF_THRESHOLD=0.005
MARKET_VOLUME_DIFF_THRESHOLD=0.02
MARKET_AMOUNT_DIFF_THRESHOLD=0.02
```

其中：

- `TUSHARE_TOKEN`：A 股主数据源，建议优先配置；
- `FRED_API_KEY`：宏观数据；
- `ALPHAVANTAGE_KEY`：全球低频行情，可选；
- `TAVILY_API_KEY`：新闻搜索和网页研究，可选；
- `POLYGON_API_KEY`：美股实时行情，可选；
- `SEC_USER_AGENT`：SEC EDGAR 请求标识，不是密钥；
- `MARKET_CROSS_VALIDATE`：是否启用主备行情交叉验证；
- `MARKET_VALIDATE_CALENDAR`：是否启用交易日历校验；
- `MARKET_CLOSE_DIFF_THRESHOLD`、`MARKET_VOLUME_DIFF_THRESHOLD`、`MARKET_AMOUNT_DIFF_THRESHOLD`：主备数据差异阈值，使用小数比例表示。

### 3. 验证配置

```powershell
python -c "from config.settings import settings; print('Tushare:', settings.has_tushare()); print('FRED:', settings.has_fred()); print('Alpha Vantage:', settings.has_alphavantage()); print('Tavily:', settings.has_tavily()); print('Polygon:', settings.has_polygon())"
```

## 目录结构

```text
AGENTS.md                 项目长期工作规范
.env                      本地密钥和运行配置，不提交
.env.example              密钥配置模板
config/                   项目配置
  settings.py             环境变量和运行设置
src/                      核心代码
  data_providers/         数据源适配器
  analysis/               指标、市场和预测逻辑
  storage/                数据存储逻辑
data/                     本地数据，不提交
  raw/                    原始响应/文件
  processed/              标准化数据
  cache/                  临时缓存
  logs/                   采集日志
reports/                  生成的分析报告，不提交
docs/                     数据源、字段和方法文档
scripts/                  可执行采集/分析脚本
tests/                    自动化测试
```

## 安全约定

- 不要把真实 API Key 写入源码、报告或 Git。
- 不要用模拟数据冒充真实行情。
- 每份分析必须标注数据截止时间、来源和实时/延迟状态。
- 详细规则请先阅读 `AGENTS.md`。




### 4. 运行采集脚本

完整验证并访问各数据源：

```powershell
.\.venv\Scripts\python.exe scripts/validate_data_sources.py
```

搜索并保存新闻：

```powershell
.\.venv\Scripts\python.exe scripts/search_market_news.py "A股市场" --time-range day --max-results 10
```

运行每日行情、新闻和 Markdown 报告流水线：

```powershell
.\.venv\Scripts\python.exe scripts/run_daily_pipeline.py --symbol 600519.SH
```

每日流水线默认向目标交易日前回看 120 个自然日，以保证 MA、MACD、RSI、布林带和 ATR 等长周期指标有足够历史数据；可用 `--lookback-days 0` 恢复仅获取目标日。

新闻原始响应保存到 `data/raw/news/`，标准化新闻保存到 `data/processed/news/`，标准行情、技术指标和报告分别保存到 `data/processed/market/`、`data/processed/market/indicators/` 与 `reports/`。备用行情源的原始响应保存到 `data/raw/market/{source}/{date}/`，主备交叉验证明细保存到 `data/processed/market/audit/`，不可用或不一致状态也会保留错误与待核验信息。

## 预测输入质量门禁

每日行情流水线在保存标准化行情后，会执行预测输入质量门禁，结果写入行情质量报告和每日 Markdown 报告：

- `approved`：主源质量检查通过，且主备交叉验证正常，可以进入预测；
- `degraded`：主源可用，但存在备用源不可用、交叉验证跳过、路由降级或普通质量警告，可以进入预测但必须降低可信度并保留告警；
- `blocked`：行情为空、主源质量错误、交易日历异常或主备数据不一致，不得进入预测。

预测入口可以调用 `src.analysis.require_prediction_input`，在 `blocked` 时抛出 `PredictionInputBlockedError`，避免业务逻辑绕过门禁。

技术指标入口 `src.analysis.calculate_technical_indicators` 会强制执行上述门禁，并计算均线、MACD、RSI、布林带、ATR、滚动支撑/压力位和波动率。技术信号入口 `src.analysis.generate_technical_signals` 将这些指标转换为可解释的趋势、MACD、RSI、布林带、支撑/压力和波动率信号，输出综合方向、信号强度、置信度、触发条件和失效条件；每日流水线会把信号数据保存到 `data/processed/market/signals/` 并写入日报。

## 次日预测层

每日流水线在技术指标和技术信号之后生成次日（T+1）条件性预测：

```python
from src.analysis import generate_next_day_prediction

prediction = generate_next_day_prediction(
    technical_signals,
    symbol="600519.SH",
)
```

默认采用四维框架：大盘环境 30%、资金行为 30%、技术面 25%、消息面 15%。大盘环境和资金行为已支持真实数据接入；当对应数据源不可用时，维度会明确标记为不可用，不会用默认值或模拟数据填充。有效维度权重会重新归一化，结果会标注数据覆盖率和条件性估计限制。覆盖率不足时置信度会自动封顶，概率是模型估计，不是收益承诺。

预测结果包括方向概率、综合方向、置信度、预期变动、ATR/波动率估算价格区间、触发条件、失效条件、缺失维度和数据质量警告。每日流水线会将结果保存到 `data/processed/market/predictions/`，文件名为 `{symbol}_{trade_date}_next_day_prediction.json`；同一日期重复运行不会覆盖既有结果。

预测链路严格为：行情采集 → 统一标准化 → 数据质量门禁 → 技术指标 → 技术信号 → 次日预测 → 结果存储 → 中文日报。`blocked` 行情不得进入指标、信号或预测；`degraded` 行情可以继续，但必须保留告警并降低置信度。

### 大盘环境评分

每日流水线会优先通过支持指数接口的行情供应商获取上证指数（`000001.SH`）、沪深 300（`000300.SH`）和创业板指（`399006.SZ`），经过与个股相同的标准化、交易日历、质量检查和主备交叉验证后，计算大盘环境评分。评分使用指数相对 20 日均线的趋势和 5 日动量；若提供真实的上涨/下跌家数，还会加入市场宽度评分。

指数行情和原始响应保存到 `data/processed/market/` 与 `data/raw/market/`，日报中的 `#### 大盘环境` 会展示评分、共同数据截止日、指数证据、来源和质量警告。指数数据获取失败不会伪造评分；次日预测会保留大盘环境缺失告警并自动按可用维度降权。

### 资金行为评分

每日流水线会通过 `DataSourceRouter.fetch_fund_flow` 获取个股日资金流向，优先使用 Tushare Pro `moneyflow`，并以 AKShare 个股资金流接口作为研究型备用源。资金流向会统一为人民币元，检查交易日、净流入金额、重复记录和主备差异，再由 `src.analysis.evaluate_fund_flow` 按最近 20 日净流入强度与近 5 日方向一致性生成 -1 到 +1 评分。

资金流向原始响应、标准化数据、质量报告和主备审计分别保存到 `data/raw/fund_flow/`、`data/processed/fund_flow/`；日报中的 `#### 资金行为` 会展示评分、最新主力净流入、数据截止时间、证据和告警。资金接口失败时不阻断技术面分析，次日预测会标记资金维度不可用并按可用维度降权。

本阶段新增融资融券和龙虎榜补充证据：`fetch_margin` 使用 Tushare `margin_detail` 为主、AKShare 可用接口为研究型备用；`fetch_dragon_tiger` 使用 Tushare `top_list` 为主、AKShare `stock_lhb_detail_em` 为备用。两类数据均统一字段、检查日期/数值/重复记录、保存原始响应和主备审计，并以资金行为维度的可选子项纳入评分，不会把缺失数据当作 0；Tushare 融资融券/龙虎榜金额按元处理，融券卖出和偿还按股数保留。融资融券原始/处理数据保存在 `data/raw/margin/`、`data/processed/margin/`，龙虎榜对应 `data/raw/dragon_tiger/`、`data/processed/dragon_tiger/`。


### 消息面评分与信息截面

每日流水线会在生成个股预测前获取 Tavily 新闻，先按发布时间执行信息截面过滤：目标交易日使用 `Asia/Shanghai` 当日收盘后的本地日期截面；缺少或无法解析 `published_at` 的新闻只保存审计，不进入评分；晚于截面的新闻会被排除，防止未来函数。

`src.analysis.evaluate_news` 使用可解释的利好/利空关键词基线生成 -1 到 +1 的消息面评分，并将有效新闻数、排除数、关键词证据、评分规则版本和告警传入次日预测。新闻原始响应和带截面状态的标准化记录保存到 `data/raw/news/` 与 `data/processed/news/`。未配置 `TAVILY_API_KEY` 或新闻接口失败时，消息面标记为不可用，预测自动按实际可用维度重新归一化。
