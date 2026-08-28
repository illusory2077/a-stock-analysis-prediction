# 采集脚本

## 验证数据源

```powershell
.\.venv\Scripts\python.exe scripts/validate_data_sources.py
```

## 搜索新闻

```powershell
.\.venv\Scripts\python.exe scripts/search_market_news.py "贵州茅台" --symbols 600519.SH --time-range week
```

脚本会：

1. 调用数据源路由器；
2. 保存 Tavily 原始响应到 `data/raw/news/`；
3. 标准化和去重后追加到 `data/processed/news/*.jsonl`；
4. 输出来源、是否降级和保存路径。

## 每日流水线

```powershell
.\.venv\Scripts\python.exe scripts/run_daily_pipeline.py --symbol 600519.SH 000858.SZ
```

每日流水线会采集行情、搜索市场新闻、保存数据，并生成 `reports/daily_YYYY-MM-DD.md`。单个数据源失败时会记录失败信息；只要仍有行情或新闻成功，流水线会生成报告。
