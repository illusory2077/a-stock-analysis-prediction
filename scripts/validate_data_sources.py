from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def result(name: str, status: str, detail: str = "") -> None:
    suffix = f" - {detail}" if detail else ""
    print(f"[{status:<7}] {name}{suffix}")


def request_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:180]


def validate_tushare() -> None:
    token = os.getenv("TUSHARE_TOKEN", "")
    if not token:
        result("Tushare Pro", "SKIP", "TUSHARE_TOKEN 未填写")
        return
    try:
        response = requests.post(
            "https://api.tushare.pro",
            json={
                "api_name": "trade_cal",
                "token": token,
                "params": {"exchange": "SSE", "start_date": "20260818", "end_date": "20260818"},
                "fields": "exchange,cal_date,is_open",
            },
            timeout=20,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        if body.get("code") == 0:
            result("Tushare Pro", "PASS", "Token 有效，trade_cal 请求成功")
        else:
            result("Tushare Pro", "FAIL", str(body.get("msg") or body.get("message") or body)[:180])
    except Exception as exc:  # noqa: BLE001
        result("Tushare Pro", "FAIL", request_error(exc))


def validate_fred() -> None:
    key = os.getenv("FRED_API_KEY", "")
    if not key:
        result("FRED", "SKIP", "FRED_API_KEY 未填写")
        return
    try:
        response = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "api_key": key,
                "file_type": "json",
                "series_id": "DFF",
                "limit": 1,
                "sort_order": "desc",
            },
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        if "observations" in body:
            result("FRED", "PASS", "DFF 序列请求成功")
        else:
            result("FRED", "FAIL", str(body.get("error_message") or body)[:180])
    except Exception as exc:  # noqa: BLE001
        result("FRED", "FAIL", request_error(exc))


def validate_alphavantage() -> None:
    key = os.getenv("ALPHAVANTAGE_KEY", "")
    if not key:
        result("Alpha Vantage", "SKIP", "ALPHAVANTAGE_KEY 未填写")
        return
    try:
        response = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "GLOBAL_QUOTE", "symbol": "IBM", "apikey": key},
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        if "Global Quote" in body:
            result("Alpha Vantage", "PASS", "GLOBAL_QUOTE 请求成功")
        elif "Note" in body:
            result("Alpha Vantage", "WARN", "Key 可到达服务，但触发频率限制")
        else:
            result("Alpha Vantage", "FAIL", str(body.get("Information") or body.get("Error Message") or body)[:180])
    except Exception as exc:  # noqa: BLE001
        result("Alpha Vantage", "FAIL", request_error(exc))


def validate_tavily() -> None:
    key = os.getenv("TAVILY_API_KEY", "")
    if not key:
        result("Tavily", "SKIP", "TAVILY_API_KEY 未填写（可选）")
        return
    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": key,
                "query": "A股市场新闻",
                "topic": "news",
                "search_depth": "basic",
                "max_results": 1,
                "include_answer": False,
            },
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        if isinstance(body.get("results"), list):
            result("Tavily", "PASS", f"新闻搜索请求成功，返回 {len(body['results'])} 条结果")
        else:
            result("Tavily", "FAIL", str(body.get("detail") or body)[:180])
    except Exception as exc:  # noqa: BLE001
        result("Tavily", "FAIL", request_error(exc))


def validate_polygon() -> None:
    key = os.getenv("POLYGON_API_KEY", "")
    if not key:
        result("Polygon", "SKIP", "POLYGON_API_KEY 未填写（可选）")
        return
    try:
        response = requests.get(
            "https://api.polygon.io/v3/reference/tickers/AAPL",
            params={"apiKey": key},
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("status") == "OK" or body.get("results"):
            result("Polygon", "PASS", "ticker reference 请求成功")
        else:
            result("Polygon", "FAIL", str(body)[:180])
    except Exception as exc:  # noqa: BLE001
        result("Polygon", "FAIL", request_error(exc))


def validate_sec() -> None:
    user_agent = os.getenv("SEC_USER_AGENT", "")
    if not user_agent or "your_email@example.com" in user_agent:
        result("SEC EDGAR", "FAIL", "SEC_USER_AGENT 需要填写真实联系邮箱")
        return
    try:
        response = requests.get(
            "https://data.sec.gov/submissions/CIK0000320193.json",
            headers={"User-Agent": user_agent},
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        if body.get(" cik ".strip()) == "0000320193" or body.get("name"):
            result("SEC EDGAR", "PASS", "公司提交记录请求成功")
        else:
            result("SEC EDGAR", "FAIL", "返回内容不符合预期")
    except Exception as exc:  # noqa: BLE001
        result("SEC EDGAR", "FAIL", request_error(exc))


def validate_akshare() -> None:
    if importlib.util.find_spec("akshare") is None:
        result("AKShare", "FAIL", "未安装 akshare，请先安装 requirements.txt")
        return
    try:
        import akshare as ak

        df = ak.stock_zh_a_hist(
            symbol="600519",
            period="daily",
            start_date="20200102",
            end_date="20200103",
            adjust="",
        )
        if getattr(df, "empty", True):
            result("AKShare", "WARN", "已安装，但测试区间未返回数据")
        else:
            result("AKShare", "PASS", f"stock_zh_a_hist 返回 {len(df)} 条记录")
    except Exception as exc:  # noqa: BLE001
        result("AKShare", "FAIL", request_error(exc))


def main() -> int:
    load_dotenv(ROOT / ".env")
    print("数据源配置验证（不会输出任何秘钥）")
    print(f"项目目录: {ROOT}")
    validate_tushare()
    validate_akshare()
    validate_fred()
    validate_alphavantage()
    validate_tavily()
    validate_polygon()
    validate_sec()
    print("验证结束。PASS=可用，WARN=可访问但有限制，SKIP=未配置，FAIL=需要处理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

