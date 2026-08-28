from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import settings

DATE = datetime.now().astimezone().date().isoformat()
RAW_DIR = ROOT / "data" / "raw" / "market" / "tencent" / DATE
PROCESSED_DIR = ROOT / "data" / "processed" / "market"
RAW_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = {
    "sz159876": "有色ETF", "sh601899": "紫金矿业", "sh600362": "江西铜业",
    "sh603993": "洛阳钼业", "sh601609": "金田股份", "sh601600": "中国铝业",
    "sz000807": "云铝股份", "sz000933": "神火股份", "sz002532": "天山铝业",
    "sh600547": "山东黄金", "sh600489": "中金黄金", "sh600988": "赤峰黄金",
    "sz000975": "银泰黄金", "sh600111": "北方稀土", "sz000831": "中国稀土",
    "sh600392": "盛和资源", "sh600259": "广晟有色", "sz000960": "锡业股份",
    "sh600549": "厦门钨业", "sh600301": "华锡有色", "sz002460": "赣锋锂业",
    "sz002466": "天齐锂业", "sh000001": "上证指数", "sz399006": "创业板指",
    "sh000300": "沪深300",
}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AStockAnalysis/1.0)"}

def fetch_quote() -> dict:
    url = "https://qt.gtimg.cn/q=" + ",".join(SYMBOLS)
    text = requests.get(url, headers=HEADERS, timeout=20).text
    (RAW_DIR / "quotes.txt").write_text(text, encoding="utf-8")
    rows = []
    for symbol, name in SYMBOLS.items():
        prefix = f'v_{symbol}="'
        start = text.find(prefix)
        if start < 0:
            continue
        start += len(prefix)
        end = text.find('";', start)
        values = text[start:end].split("~")
        if len(values) < 39:
            continue
        rows.append({
            "symbol": symbol, "name": name, "close": float(values[3]),
            "prev_close": float(values[4]), "open": float(values[5]),
            "volume": float(values[6]), "amount": float(values[37]),
            "timestamp": values[30], "change": float(values[31]),
            "pct_change": float(values[32]), "source": "Tencent quote API",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        })
    return {"source": "Tencent quote API", "retrieved_at": datetime.now(timezone.utc).isoformat(), "rows": rows}

def fetch_history() -> list[dict]:
    out = []
    for symbol, name in SYMBOLS.items():
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,80,qfq"
        payload = requests.get(url, headers=HEADERS, timeout=20).json()
        (RAW_DIR / f"{symbol}_history.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        data = payload.get("data", {}).get(symbol, {}).get("qfqday", [])
        if not data:
            continue
        data = [row[:6] for row in data]
        frame = pd.DataFrame(data, columns=["trade_date", "open", "close", "high", "low", "volume"])
        for col in ["open", "close", "high", "low", "volume"]:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
        if (frame["high"] < frame[["open", "close"]].max(axis=1)).any() or (frame["low"] > frame[["open", "close"]].min(axis=1)).any():
            raise ValueError(f"OHLC quality check failed for {symbol}")
        last = frame.iloc[-1]
        def ret(n: int) -> float:
            return float((last.close / frame.iloc[-1 - n].close - 1) * 100)
        out.append({
            "symbol": symbol, "name": name, "trade_date": str(last.trade_date.date()),
            "close_qfq": float(last.close), "return_5d_pct": ret(5), "return_20d_pct": ret(20),
            "ma20_qfq": float(frame.close.tail(20).mean()), "ma60_qfq": float(frame.close.tail(60).mean()),
            "volume_ratio_vs_5d_avg": float(last.volume / frame.volume.tail(5).mean()),
            "source": "Tencent fqkline API", "retrieved_at": datetime.now(timezone.utc).isoformat(),
        })
    return out

def fetch_commodities() -> dict:
    result = {}
    for function in ["COPPER", "ALUMINUM"]:
        payload = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": function, "interval": "monthly", "apikey": settings.alphavantage_key},
            timeout=30,
        ).json()
        result[function] = payload
    (RAW_DIR / "alphavantage_commodities.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result

if __name__ == "__main__":
    quotes = fetch_quote()
    history = fetch_history()
    commodities = fetch_commodities()
    processed = {
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "quote_source": quotes["source"], "history_source": "Tencent fqkline API",
        "quotes": quotes["rows"], "history": history,
        "commodities": {k: v.get("data", [])[:12] for k, v in commodities.items()},
    }
    path = PROCESSED_DIR / f"nonferrous_{DATE}.json"
    path.write_text(json.dumps(processed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)
