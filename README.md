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
```

其中：

- `TUSHARE_TOKEN`：A 股主数据源，建议优先配置；
- `FRED_API_KEY`：宏观数据；
- `ALPHAVANTAGE_KEY`：全球低频行情，可选；
- `TAVILY_API_KEY`：新闻搜索和网页研究，可选；
- `POLYGON_API_KEY`：美股实时行情，可选；
- `SEC_USER_AGENT`：SEC EDGAR 请求标识，不是密钥。

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

新闻原始响应保存到 `data/raw/news/`，标准化新闻保存到 `data/processed/news/`，行情和报告分别保存到 `data/processed/market/` 与 `reports/`。
