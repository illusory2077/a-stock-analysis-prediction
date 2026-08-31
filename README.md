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
