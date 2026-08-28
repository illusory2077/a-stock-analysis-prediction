from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# Project root when executed as `python scripts/focus_sector_report.py`
ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = ROOT / "data" / "raw" / "market"
DATA_PROCESSED = ROOT / "data" / "processed" / "market"
REPORTS = ROOT / "reports"
TRADE_DATE = "2026-08-19"
START_DATE = "20260701"
END_DATE = "20260819"

FOCUS_GROUPS: dict[str, list[str]] = {
    "电子": ["半导体", "元件", "光学光电子", "消费电子", "其他电子", "电子化学品"],
    "银行": ["银行"],
    "电力设备": ["电池", "光伏设备", "风电设备", "电网设备", "其他电源设备", "电机"],
    "通信": ["通信服务", "通信设备"],
    "医药生物": ["化学制药", "中药", "生物制品", "医疗器械", "医疗服务", "医药商业"],
    "非银金融": ["证券", "保险", "多元金融"],
    "有色金属": ["贵金属", "工业金属", "能源金属", "小金属", "金属新材料"],
}


def fetch_history(ak, symbol: str) -> pd.DataFrame:
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            df = ak.stock_board_industry_index_ths(
                symbol=symbol, start_date=START_DATE, end_date=END_DATE
            )
            if df is None or df.empty:
                raise RuntimeError(f"{symbol} returned empty history")
            return df
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {symbol}: {last_exc}")


def clean_num(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def summarize(df: pd.DataFrame, symbol: str) -> dict:
    df = df.copy()
    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    for col in ["开盘价", "最高价", "最低价", "收盘价", "成交量", "成交额"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    row = df[df["日期"] == TRADE_DATE]
    if row.empty:
        raise RuntimeError(f"{symbol} missing {TRADE_DATE}")
    idx = row.index[-1]
    pos = df.index.get_loc(idx)
    if pos < 1:
        raise RuntimeError(f"{symbol} insufficient history")
    close = float(df.loc[idx, "收盘价"])
    prev_close = float(df.iloc[pos - 1]["收盘价"])
    day_return = close / prev_close - 1
    def trailing_return(n: int):
        if pos < n:
            return None
        return close / float(df.iloc[pos - n]["收盘价"]) - 1
    recent = df.iloc[max(0, pos - 19): pos + 1]
    prior5 = df.iloc[max(0, pos - 5): pos]
    ma20 = float(recent["收盘价"].mean())
    volume = float(df.loc[idx, "成交量"])
    avg5_volume = float(prior5["成交量"].mean()) if not prior5.empty else None
    return {
        "symbol": symbol,
        "trade_date": TRADE_DATE,
        "open": clean_num(df.loc[idx, "开盘价"]),
        "high": clean_num(df.loc[idx, "最高价"]),
        "low": clean_num(df.loc[idx, "最低价"]),
        "close": close,
        "prev_close": prev_close,
        "day_return_pct": day_return * 100,
        "return_5d_pct": trailing_return(5) * 100 if trailing_return(5) is not None else None,
        "return_20d_pct": trailing_return(20) * 100 if trailing_return(20) is not None else None,
        "ma20": ma20,
        "vs_ma20_pct": (close / ma20 - 1) * 100,
        "volume": volume,
        "amount": float(df.loc[idx, "成交额"]),
        "volume_ratio_5d": volume / avg5_volume if avg5_volume else None,
        "high_20d": float(recent["最高价"].max()),
        "low_20d": float(recent["最低价"].min()),
        "raw_history": df.to_dict(orient="records"),
    }


def main() -> None:
    sys.path.insert(0, str(ROOT))
    import akshare as ak

    retrieved_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    raw: dict = {
        "analysis_date": TRADE_DATE,
        "retrieved_at": retrieved_at,
        "status": "收盘后历史数据",
        "source": "同花顺行业指数历史接口（通过AKShare）",
        "source_url": "https://q.10jqka.com.cn/thshy/",
        "primary_source_attempt": {"provider": "Tushare Pro", "status": "failed", "reason": "当前Token无sw_daily/index_classify接口权限"},
        "degraded": True,
        "groups": {},
    }
    summaries: dict[str, list[dict]] = {}
    for group, symbols in FOCUS_GROUPS.items():
        summaries[group] = []
        for symbol in symbols:
            print(f"fetching {group}/{symbol}", flush=True)
            df = fetch_history(ak, symbol)
            item = summarize(df, symbol)
            summaries[group].append(item)
            # Store raw rows separately but keep metadata and transformed summary together.
            time.sleep(0.15)
        raw["groups"][group] = summaries[group]

    DATA_RAW.mkdir(parents=True, exist_ok=True)
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    (DATA_RAW / f"focus-sectors-{TRADE_DATE}.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    group_rows = []
    detail_rows = []
    for group, items in summaries.items():
        def avg(key):
            vals = [x[key] for x in items if x.get(key) is not None]
            return statistics.mean(vals) if vals else None
        group_rows.append({
            "group": group,
            "constituent_count": len(items),
            "equal_weight_day_return_pct": avg("day_return_pct"),
            "equal_weight_return_5d_pct": avg("return_5d_pct"),
            "equal_weight_return_20d_pct": avg("return_20d_pct"),
            "equal_weight_vs_ma20_pct": avg("vs_ma20_pct"),
            "total_amount_yuan": sum(x["amount"] for x in items),
            "avg_volume_ratio_5d": avg("volume_ratio_5d"),
            "positive_subsectors": sum(1 for x in items if x["day_return_pct"] > 0),
            "negative_subsectors": sum(1 for x in items if x["day_return_pct"] < 0),
        })
        for item in items:
            detail_rows.append({k: v for k, v in item.items() if k != "raw_history"} | {"group": group})

    pd.DataFrame(group_rows).to_csv(DATA_PROCESSED / f"focus-sector-summary-{TRADE_DATE}.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(detail_rows).to_csv(DATA_PROCESSED / f"focus-sector-detail-{TRADE_DATE}.csv", index=False, encoding="utf-8-sig")
    print(json.dumps({"retrieved_at": retrieved_at, "groups": group_rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
