from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from config.settings import settings
from src.market import (
    CalendarError,
    TradingCalendar,
    compare_daily_bars,
    compare_fund_flow,
    compare_dragon_tiger,
    compare_margin,
    normalize_dragon_tiger,
    normalize_disclosures,
    normalize_fund_flow,
    normalize_margin,
    validate_dragon_tiger,
    validate_disclosures,
    validate_fund_flow,
    validate_margin,
    normalize_daily_bars,
    validate_daily_bars,
    validate_observed_trade_dates,
)
from .akshare_provider import AkshareProvider
from .base import DataProvider, DataProviderError
from .tavily_provider import TavilyProvider
from .tushare_provider import TushareProvider


class DataSourceRouter:
    """按优先级调用数据源，完成标准化、质量检查和主备交叉验证。"""

    def __init__(
        self,
        *,
        market_providers: Iterable[DataProvider] | None = None,
        news_providers: Iterable[Any] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        cross_validate_market: bool | None = None,
        validate_calendar: bool | None = None,
        calendar: TradingCalendar | None = None,
        close_diff_threshold: float | None = None,
        volume_diff_threshold: float | None = None,
        amount_diff_threshold: float | None = None,
    ) -> None:
        self.market_providers = list(market_providers) if market_providers is not None else self._default_market_providers()
        self.news_providers = list(news_providers) if news_providers is not None else self._default_news_providers()
        self.sleep_fn = sleep_fn
        self.cross_validate_market = settings.market_cross_validate if cross_validate_market is None else cross_validate_market
        self.validate_calendar = settings.market_validate_calendar if validate_calendar is None else validate_calendar
        self.calendar = calendar
        self.close_diff_threshold = (
            settings.market_close_diff_threshold if close_diff_threshold is None else close_diff_threshold
        )
        self.volume_diff_threshold = (
            settings.market_volume_diff_threshold if volume_diff_threshold is None else volume_diff_threshold
        )
        self.amount_diff_threshold = (
            settings.market_amount_diff_threshold if amount_diff_threshold is None else amount_diff_threshold
        )

    def fetch_daily_bars(self, symbol: str, start_date: date, end_date: date) -> dict[str, Any]:
        """获取日线，统一标准化、检查交易日，再用备用源交叉验证。"""
        if not self.market_providers:
            raise DataProviderError(f"没有可用的数据源: 获取 {symbol} 日线")

        attempts: list[dict[str, Any]] = []
        selected: dict[str, Any] | None = None
        selected_index = -1
        for index, provider in enumerate(self.market_providers):
            provider_name = getattr(provider, "name", type(provider).__name__)
            try:
                raw_data, retries = self._call_with_retry(
                    lambda provider=provider: provider.daily_bars(symbol, start_date, end_date)
                )
                if self._is_empty_result(raw_data):
                    raise DataProviderError(f"{provider_name} 返回空数据")
                retrieved_at = datetime.now(timezone.utc)
                secondary_retrieved_at = datetime.now(timezone.utc)
                normalized, quality = self._normalize_and_validate(
                    raw_data,
                    symbol=symbol,
                    provider_name=provider_name,
                    start_date=start_date,
                    end_date=end_date,
                    retrieved_at=retrieved_at,
                )
                normalized, quality = self._apply_calendar_validation(
                    normalized,
                    quality,
                    provider,
                    start_date=start_date,
                    end_date=end_date,
                )
                if quality.data.empty:
                    detail = "; ".join(quality.report.get("errors", [])) or "没有有效行情记录"
                    raise DataProviderError(f"{provider_name} 数据质量校验失败: {detail}")
                attempts.append({"provider": provider_name, "role": "primary", "ok": True, "retries": retries})
                selected = {
                    "data": quality.data,
                    "quality_report": quality.report,
                    "raw_data": raw_data,
                    "rejected_data": quality.rejected_data,
                    "retrieved_at": retrieved_at,
                    "source": provider_name,
                    "data_version": getattr(provider, "data_version", None),
                }
                selected_index = index
                break
            except Exception as exc:  # noqa: BLE001
                attempts.append(
                    {
                        "provider": provider_name,
                        "role": "primary",
                        "ok": False,
                        "error": str(exc),
                        "retryable": self._is_retryable(exc),
                    }
                )

        if selected is None:
            details = "; ".join(f"{item['provider']}: {item.get('error', 'failed')}" for item in attempts)
            raise DataProviderError(f"获取 {symbol} 日线的所有数据源均失败: {details}")

        cross_report, secondary_audits, attempts = self._cross_validate_secondary(
            selected["data"],
            selected_source=selected["source"],
            providers=self.market_providers[selected_index + 1 :],
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            attempts=attempts,
        )
        selected["quality_report"]["cross_validation"] = cross_report
        for warning in cross_report.get("warnings", []):
            if warning not in selected["quality_report"].setdefault("warnings", []):
                selected["quality_report"]["warnings"].append(warning)
        if cross_report.get("warnings") and selected["quality_report"].get("status") == "validated":
            selected["quality_report"]["status"] = "validated_with_warning"

        return {
            **selected,
            "degraded": selected_index > 0,
            "attempts": attempts,
            "secondary_audits": secondary_audits,
        }

    def fetch_index_bars(self, symbol: str, start_date: date, end_date: date) -> dict[str, Any]:
        """获取指数日线，使用支持 ``index_daily_bars`` 的行情供应商。"""
        providers = [
            provider
            for provider in self.market_providers
            if callable(getattr(provider, "index_daily_bars", None))
        ]
        if not providers:
            raise DataProviderError(f"没有可用的数据源: 获取 {symbol} 指数日线")

        attempts: list[dict[str, Any]] = []
        selected: dict[str, Any] | None = None
        selected_index = -1
        for index, provider in enumerate(providers):
            provider_name = getattr(provider, "name", type(provider).__name__)
            try:
                raw_data, retries = self._call_with_retry(
                    lambda provider=provider: provider.index_daily_bars(symbol, start_date, end_date)
                )
                if self._is_empty_result(raw_data):
                    raise DataProviderError(f"{provider_name} 返回空数据")
                retrieved_at = datetime.now(timezone.utc)
                normalized, quality = self._normalize_and_validate(
                    raw_data,
                    symbol=symbol,
                    provider_name=provider_name,
                    start_date=start_date,
                    end_date=end_date,
                    retrieved_at=retrieved_at,
                    asset_type="index",
                )
                normalized, quality = self._apply_calendar_validation(
                    normalized,
                    quality,
                    provider,
                    start_date=start_date,
                    end_date=end_date,
                )
                if quality.data.empty:
                    detail = "; ".join(quality.report.get("errors", [])) or "没有有效指数行情记录"
                    raise DataProviderError(f"{provider_name} 数据质量校验失败: {detail}")
                attempts.append({"provider": provider_name, "role": "primary", "ok": True, "retries": retries})
                selected = {
                    "data": quality.data,
                    "quality_report": quality.report,
                    "raw_data": raw_data,
                    "rejected_data": quality.rejected_data,
                    "retrieved_at": retrieved_at,
                    "source": provider_name,
                    "data_version": getattr(provider, "data_version", None),
                }
                selected_index = index
                break
            except Exception as exc:  # noqa: BLE001
                attempts.append(
                    {
                        "provider": provider_name,
                        "role": "primary",
                        "ok": False,
                        "error": str(exc),
                        "retryable": self._is_retryable(exc),
                    }
                )

        if selected is None:
            details = "; ".join(f"{item['provider']}: {item.get('error', 'failed')}" for item in attempts)
            raise DataProviderError(f"获取 {symbol} 指数日线的所有数据源均失败: {details}")

        cross_report, secondary_audits, attempts = self._cross_validate_secondary(
            selected["data"],
            selected_source=selected["source"],
            providers=providers[selected_index + 1 :],
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            attempts=attempts,
            fetch_method="index_daily_bars",
            asset_type="index",
        )
        selected["quality_report"]["cross_validation"] = cross_report
        for warning in cross_report.get("warnings", []):
            if warning not in selected["quality_report"].setdefault("warnings", []):
                selected["quality_report"]["warnings"].append(warning)
        if cross_report.get("warnings") and selected["quality_report"].get("status") == "validated":
            selected["quality_report"]["status"] = "validated_with_warning"

        return {
            **selected,
            "degraded": selected_index > 0,
            "attempts": attempts,
            "secondary_audits": secondary_audits,
        }
    def fetch_fund_flow(self, symbol: str, start_date: date, end_date: date) -> dict[str, Any]:
        """获取个股日资金流向，完成标准化、质量检查和主备交叉验证。"""
        providers = [provider for provider in self.market_providers if callable(getattr(provider, "fund_flow", None))]
        if not providers:
            raise DataProviderError(f"没有可用的数据源: 获取 {symbol} 资金流向")

        attempts: list[dict[str, Any]] = []
        selected: dict[str, Any] | None = None
        selected_index = -1
        for index, provider in enumerate(providers):
            provider_name = getattr(provider, "name", type(provider).__name__)
            try:
                raw_data, retries = self._call_with_retry(lambda provider=provider: provider.fund_flow(symbol, start_date, end_date))
                if self._is_empty_result(raw_data):
                    raise DataProviderError(f"{provider_name} 返回空数据")
                retrieved_at = datetime.now(timezone.utc)
                normalized = normalize_fund_flow(
                    raw_data, symbol=symbol, source=provider_name, retrieved_at=retrieved_at,
                    start_date=start_date, end_date=end_date, data_version=getattr(provider, "data_version", None),
                )
                quality = validate_fund_flow(
                    normalized, rejected_data=normalized.attrs.get("rejected_data"),
                    input_rows=normalized.attrs.get("input_rows"),
                    warnings=normalized.attrs.get("normalization_warnings", []),
                    column_mapping=normalized.attrs.get("column_mapping", {}),
                )
                if quality.data.empty:
                    detail = "; ".join(quality.report.get("errors", [])) or "没有有效资金流向记录"
                    raise DataProviderError(f"{provider_name} 数据质量校验失败: {detail}")
                attempts.append({"provider": provider_name, "role": "primary", "ok": True, "retries": retries})
                selected = {
                    "data": quality.data, "quality_report": quality.report, "raw_data": raw_data,
                    "rejected_data": quality.rejected_data, "retrieved_at": retrieved_at,
                    "source": provider_name, "data_version": getattr(provider, "data_version", None),
                }
                selected_index = index
                break
            except Exception as exc:  # noqa: BLE001
                attempts.append({"provider": provider_name, "role": "primary", "ok": False, "error": str(exc), "retryable": self._is_retryable(exc)})

        if selected is None:
            details = "; ".join(f"{item['provider']}: {item.get('error', 'failed')}" for item in attempts)
            raise DataProviderError(f"获取 {symbol} 资金流向的所有数据源均失败: {details}")

        cross_report, secondary_audits, attempts = self._cross_validate_fund_flow(
            selected["data"], selected_source=selected["source"], providers=providers[selected_index + 1:],
            symbol=symbol, start_date=start_date, end_date=end_date, attempts=attempts,
        )
        selected["quality_report"]["cross_validation"] = cross_report
        for warning in cross_report.get("warnings", []):
            if warning not in selected["quality_report"].setdefault("warnings", []):
                selected["quality_report"]["warnings"].append(warning)
        if cross_report.get("warnings") and selected["quality_report"].get("status") == "validated":
            selected["quality_report"]["status"] = "validated_with_warning"
        return {**selected, "degraded": selected_index > 0, "attempts": attempts, "secondary_audits": secondary_audits}

    def fetch_disclosures(self, symbol: str, start_date: date, end_date: date) -> dict[str, Any]:
        """获取公告/财报，统一字段并执行公开披露时间质量检查。"""
        providers = [provider for provider in self.market_providers if callable(getattr(provider, "disclosures", None))]
        if not providers:
            raise DataProviderError(f"没有可用的数据源: 获取 {symbol} 公告/财报")
        attempts: list[dict[str, Any]] = []
        for index, provider in enumerate(providers):
            name = getattr(provider, "name", type(provider).__name__)
            try:
                raw_data, retries = self._call_with_retry(lambda provider=provider: provider.disclosures(symbol, start_date, end_date))
                if self._is_empty_result(raw_data):
                    raise DataProviderError(f"{name} 返回空公告/财报")
                retrieved_at = datetime.now(timezone.utc)
                frames: list[pd.DataFrame] = []; rejected: list[pd.DataFrame] = []; input_rows = 0
                normalization_warnings: list[str] = []; mappings: dict[str, Any] = {}
                sections = raw_data if isinstance(raw_data, dict) else {"announcements": raw_data}
                for section, report_type in (("announcements", "announcement"), ("financial_reports", "financial_report")):
                    section_data = sections.get(section) if isinstance(sections, dict) else None
                    if self._is_empty_result(section_data): continue
                    normalized = normalize_disclosures(
                        section_data, symbol=symbol, report_type=report_type, source=name, retrieved_at=retrieved_at,
                        start_date=start_date, end_date=end_date, data_version=getattr(provider, "data_version", None),
                    )
                    frames.append(normalized); input_rows += int(normalized.attrs.get("input_rows", len(normalized)))
                    rejected_data = normalized.attrs.get("rejected_data")
                    if isinstance(rejected_data, pd.DataFrame) and not rejected_data.empty: rejected.append(rejected_data)
                    normalization_warnings.extend(normalized.attrs.get("normalization_warnings", [])); mappings[report_type] = normalized.attrs.get("column_mapping", {})
                if not frames: raise DataProviderError(f"{name} 未返回公告或财报记录")
                for frame in frames: frame.attrs = {}
                for frame in rejected: frame.attrs = {}
                data = pd.concat(frames, ignore_index=True, sort=False); data.attrs = {}
                rejected_data = pd.concat(rejected, ignore_index=True, sort=False) if rejected else None
                if rejected_data is not None: rejected_data.attrs = {}
                quality = validate_disclosures(data, rejected_data=rejected_data, input_rows=input_rows, warnings=normalization_warnings, column_mapping=mappings)
                if quality.data.empty:
                    detail = "; ".join(quality.report.get("errors", [])) or "没有有效公告/财报记录"
                    raise DataProviderError(f"{name} 数据质量校验失败: {detail}")
                attempts.append({"provider": name, "role": "primary", "ok": True, "retries": retries})
                return {"data": quality.data, "quality_report": quality.report, "raw_data": raw_data, "rejected_data": quality.rejected_data, "retrieved_at": retrieved_at, "source": name, "data_version": getattr(provider, "data_version", None), "degraded": index > 0, "attempts": attempts, "secondary_audits": []}
            except Exception as exc:  # noqa: BLE001
                attempts.append({"provider": name, "role": "primary", "ok": False, "error": str(exc), "retryable": self._is_retryable(exc)})
        details = "; ".join(f"{item['provider']}: {item.get('error', 'failed')}" for item in attempts)
        raise DataProviderError(f"获取 {symbol} 公告/财报的所有数据源均失败: {details}")

    def fetch_margin(self, symbol: str, start_date: date, end_date: date) -> dict[str, Any]:
        """获取融资融券明细，完成标准化、质量检查和主备交叉验证。"""
        return self._fetch_special(
            symbol, start_date, end_date,
            fetch_method="margin", label="融资融券",
            normalize_fn=normalize_margin, validate_fn=validate_margin,
            compare_fn=compare_margin,
        )

    def fetch_dragon_tiger(self, symbol: str, start_date: date, end_date: date) -> dict[str, Any]:
        """获取龙虎榜明细，完成标准化、质量检查和主备交叉验证。"""
        return self._fetch_special(
            symbol, start_date, end_date,
            fetch_method="dragon_tiger", label="龙虎榜",
            normalize_fn=normalize_dragon_tiger, validate_fn=validate_dragon_tiger,
            compare_fn=compare_dragon_tiger,
        )

    def _fetch_special(
        self, symbol: str, start_date: date, end_date: date, *, fetch_method: str,
        label: str, normalize_fn: Callable[..., pd.DataFrame], validate_fn: Callable[..., Any],
        compare_fn: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        providers = [provider for provider in self.market_providers if callable(getattr(provider, fetch_method, None))]
        if not providers:
            raise DataProviderError(f"没有可用的数据源: 获取 {symbol} {label}")
        attempts: list[dict[str, Any]] = []
        selected: dict[str, Any] | None = None
        selected_index = -1
        for index, provider in enumerate(providers):
            provider_name = getattr(provider, "name", type(provider).__name__)
            try:
                raw_data, retries = self._call_with_retry(lambda provider=provider: getattr(provider, fetch_method)(symbol, start_date, end_date))
                if self._is_empty_result(raw_data):
                    raise DataProviderError(f"{provider_name} 返回空数据")
                retrieved_at = datetime.now(timezone.utc)
                normalized = normalize_fn(raw_data, symbol=symbol, source=provider_name, retrieved_at=retrieved_at, start_date=start_date, end_date=end_date, data_version=getattr(provider, "data_version", None))
                quality = validate_fn(normalized, rejected_data=normalized.attrs.get("rejected_data"), input_rows=normalized.attrs.get("input_rows"), warnings=normalized.attrs.get("normalization_warnings", []), column_mapping=normalized.attrs.get("column_mapping", {}))
                if quality.data.empty:
                    detail = "; ".join(quality.report.get("errors", [])) or f"没有有效{label}记录"
                    raise DataProviderError(f"{provider_name} 数据质量校验失败: {detail}")
                attempts.append({"provider": provider_name, "role": "primary", "ok": True, "retries": retries})
                selected = {"data": quality.data, "quality_report": quality.report, "raw_data": raw_data, "rejected_data": quality.rejected_data, "retrieved_at": retrieved_at, "source": provider_name, "data_version": getattr(provider, "data_version", None)}
                selected_index = index
                break
            except Exception as exc:  # noqa: BLE001
                attempts.append({"provider": provider_name, "role": "primary", "ok": False, "error": str(exc), "retryable": self._is_retryable(exc)})
        if selected is None:
            details = "; ".join(f"{item['provider']}: {item.get('error', 'failed')}" for item in attempts)
            raise DataProviderError(f"获取 {symbol} {label}的所有数据源均失败: {details}")
        audits: list[dict[str, Any]] = []
        details: list[dict[str, Any]] = []
        if self.cross_validate_market and providers[selected_index + 1:]:
            for provider in providers[selected_index + 1:]:
                provider_name = getattr(provider, "name", type(provider).__name__)
                try:
                    raw_data, retries = self._call_with_retry(lambda provider=provider: getattr(provider, fetch_method)(symbol, start_date, end_date))
                    if self._is_empty_result(raw_data):
                        raise DataProviderError(f"{provider_name} 返回空数据")
                    secondary_retrieved_at = datetime.now(timezone.utc)
                    normalized = normalize_fn(raw_data, symbol=symbol, source=provider_name, retrieved_at=secondary_retrieved_at, start_date=start_date, end_date=end_date, data_version=getattr(provider, "data_version", None))
                    quality = validate_fn(normalized, rejected_data=normalized.attrs.get("rejected_data"), input_rows=normalized.attrs.get("input_rows"), warnings=normalized.attrs.get("normalization_warnings", []), column_mapping=normalized.attrs.get("column_mapping", {}))
                    comparison = compare_fn(selected["data"], quality.data, primary_source=selected["source"], secondary_source=provider_name, amount_threshold=self.amount_diff_threshold)
                    details.append(comparison)
                    audits.append({"source": provider_name, "status": comparison.get("status", "unavailable"), "raw_data": raw_data, "retrieved_at": secondary_retrieved_at, "data_version": getattr(provider, "data_version", None), "quality_report": quality.report, "comparison": comparison})
                    attempts.append({"provider": provider_name, "role": "secondary_validation", "ok": True, "retries": retries, "quality_status": quality.report.get("status")})
                except Exception as exc:  # noqa: BLE001
                    warning = f"备用源 {provider_name} {label}交叉验证不可用: {exc}"
                    detail = {"status": "unavailable", "primary_source": selected["source"], "secondary_source": provider_name, "common_rows": 0, "metrics": {}, "warnings": [warning]}
                    details.append(detail)
                    audits.append({"source": provider_name, "status": "unavailable", "raw_data": None, "retrieved_at": datetime.now(timezone.utc), "data_version": getattr(provider, "data_version", None), "quality_report": None, "comparison": detail, "error": str(exc)})
                    attempts.append({"provider": provider_name, "role": "secondary_validation", "ok": False, "error": str(exc), "retryable": self._is_retryable(exc)})
            statuses = {item.get("status") for item in details}
            cross_status = "mismatch" if "mismatch" in statuses else "matched" if "matched" in statuses else "unavailable"
            cross_report = {"status": cross_status, "primary_source": selected["source"], "secondary_sources": [item.get("secondary_source") for item in details], "details": details, "warnings": list(dict.fromkeys(w for item in details for w in item.get("warnings", [])))}
        else:
            cross_report = {"status": "skipped", "primary_source": selected["source"], "secondary_sources": [], "details": [], "warnings": [f"没有执行备用{label}交叉验证"]}
        selected["quality_report"]["cross_validation"] = cross_report
        for warning in cross_report.get("warnings", []):
            if warning not in selected["quality_report"].setdefault("warnings", []): selected["quality_report"]["warnings"].append(warning)
        if cross_report.get("warnings") and selected["quality_report"].get("status") == "validated": selected["quality_report"]["status"] = "validated_with_warning"
        return {**selected, "degraded": selected_index > 0, "attempts": attempts, "secondary_audits": audits}

    def search_news(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """按新闻数据源优先级搜索，返回原始响应及路由元数据。"""
        return self._run_with_fallback(
            self.news_providers,
            lambda provider: provider.search_news(query, **kwargs),
            operation_name=f"搜索新闻: {query}",
        )

    def healthcheck(self) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        for provider in [*self.market_providers, *self.news_providers]:
            try:
                checks.append(provider.healthcheck())
            except Exception as exc:  # noqa: BLE001
                checks.append(
                    {
                        "provider": getattr(provider, "name", type(provider).__name__),
                        "configured": False,
                        "ok": False,
                        "error": str(exc),
                    }
                )
        return checks

    def _normalize_and_validate(
        self,
        raw_data: Any,
        *,
        symbol: str,
        provider_name: str,
        start_date: date,
        end_date: date,
        retrieved_at: datetime,
        asset_type: str | None = None,
    ) -> tuple[pd.DataFrame, Any]:
        normalized = normalize_daily_bars(
            raw_data,
            symbol=symbol,
            source=provider_name,
            retrieved_at=retrieved_at,
            start_date=start_date,
            end_date=end_date,
            asset_type=asset_type,
        )
        quality = validate_daily_bars(
            normalized,
            rejected_data=normalized.attrs.get("rejected_data"),
            input_rows=normalized.attrs.get("input_rows"),
            warnings=normalized.attrs.get("normalization_warnings", []),
            column_mapping=normalized.attrs.get("column_mapping", {}),
        )
        if quality.data.empty:
            detail = "; ".join(quality.report.get("errors", [])) or "没有有效行情记录"
            raise DataProviderError(f"{provider_name} 数据质量校验失败: {detail}")
        return quality.data, quality

    def _apply_calendar_validation(
        self,
        data: pd.DataFrame,
        quality: Any,
        provider: Any,
        *,
        start_date: date,
        end_date: date,
    ) -> tuple[pd.DataFrame, Any]:
        if not self.validate_calendar:
            quality.report["calendar_validation"] = {"status": "skipped", "reason": "已关闭交易日历校验"}
            return data, quality
        calendar = self.calendar or TradingCalendar(provider)
        exchange = str(data["exchange"].dropna().iloc[0]) if "exchange" in data and not data["exchange"].dropna().empty else "SSE"
        try:
            open_days = calendar.open_days(start_date, end_date, exchange=exchange)
        except CalendarError as exc:
            quality.report["calendar_validation"] = {
                "status": "unavailable",
                "exchange": exchange,
                "warnings": [str(exc)],
            }
            return data, quality
        calendar_result = validate_observed_trade_dates(
            data,
            open_days=open_days,
            requested_start=start_date,
            requested_end=end_date,
        )
        if not calendar_result.rejected_data.empty:
            quality.rejected_data = _concat_frames(quality.rejected_data, calendar_result.rejected_data)
            quality.report["output_rows"] = len(calendar_result.data)
            quality.report["rejected_rows"] = len(quality.rejected_data)
            quality.report["warnings"] = list(
                dict.fromkeys([*quality.report.get("warnings", []), *calendar_result.report.get("warnings", [])])
            )
            if calendar_result.data.empty:
                quality.report["status"] = "rejected"
            elif quality.report.get("status") == "validated":
                quality.report["status"] = "validated_with_warning"
        quality.report["calendar_validation"] = {
            **calendar_result.report,
            "exchange": exchange,
        }
        quality.data = calendar_result.data
        return calendar_result.data, quality

    def _cross_validate_secondary(
        self,
        primary: pd.DataFrame,
        *,
        selected_source: str,
        providers: list[Any],
        symbol: str,
        start_date: date,
        end_date: date,
        attempts: list[dict[str, Any]],
        fetch_method: str = "daily_bars",
        asset_type: str | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        if not self.cross_validate_market or not providers:
            return {
                "status": "skipped",
                "primary_source": selected_source,
                "secondary_sources": [],
                "details": [],
                "warnings": ["没有执行备用行情源交叉验证"],
            }, [], attempts

        details: list[dict[str, Any]] = []
        secondary_audits: list[dict[str, Any]] = []
        for provider in providers:
            provider_name = getattr(provider, "name", type(provider).__name__)
            try:
                raw_data, retries = self._call_with_retry(
                    lambda provider=provider: getattr(provider, fetch_method)(symbol, start_date, end_date)
                )
                if self._is_empty_result(raw_data):
                    raise DataProviderError(f"{provider_name} 返回空数据")
                secondary_retrieved_at = datetime.now(timezone.utc)
                normalized, quality = self._normalize_and_validate(
                    raw_data,
                    symbol=symbol,
                    provider_name=provider_name,
                    start_date=start_date,
                    end_date=end_date,
                    retrieved_at=secondary_retrieved_at,
                    asset_type=asset_type,
                )
                comparison = compare_daily_bars(
                    primary,
                    quality.data,
                    primary_source=selected_source,
                    secondary_source=provider_name,
                    close_threshold=self.close_diff_threshold,
                    volume_threshold=self.volume_diff_threshold,
                    amount_threshold=self.amount_diff_threshold,
                )
                details.append(comparison)
                secondary_audits.append(
                    {
                        "source": provider_name,
                        "status": comparison.get("status", "unavailable"),
                        "raw_data": raw_data,
                        "retrieved_at": secondary_retrieved_at,
                        "data_version": getattr(provider, "data_version", None),
                        "quality_report": quality.report,
                        "comparison": comparison,
                    }
                )
                attempts.append(
                    {
                        "provider": provider_name,
                        "role": "secondary_validation",
                        "ok": True,
                        "retries": retries,
                        "quality_status": quality.report.get("status"),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                warning = f"备用源 {provider_name} 交叉验证不可用: {exc}"
                unavailable_detail = {
                    "status": "unavailable",
                    "primary_source": selected_source,
                    "secondary_source": provider_name,
                    "compared_rows": 0,
                    "missing_primary_rows": 0,
                    "missing_secondary_rows": 0,
                    "metrics": {},
                    "warnings": [warning],
                }
                details.append(unavailable_detail)
                secondary_audits.append(
                    {
                        "source": provider_name,
                        "status": "unavailable",
                        "raw_data": None,
                        "retrieved_at": datetime.now(timezone.utc),
                        "data_version": getattr(provider, "data_version", None),
                        "quality_report": None,
                        "comparison": unavailable_detail,
                        "error": str(exc),
                    }
                )
                attempts.append(
                    {
                        "provider": provider_name,
                        "role": "secondary_validation",
                        "ok": False,
                        "error": str(exc),
                        "retryable": self._is_retryable(exc),
                    }
                )

        statuses = {item.get("status") for item in details}
        if "mismatch" in statuses:
            status = "mismatch"
        elif "matched" in statuses:
            status = "matched"
        else:
            status = "unavailable"
        warnings = list(dict.fromkeys(warning for item in details for warning in item.get("warnings", [])))
        return {
            "status": status,
            "primary_source": selected_source,
            "secondary_sources": [item.get("secondary_source") for item in details],
            "details": details,
            "warnings": warnings,
        }, secondary_audits, attempts

    def _cross_validate_fund_flow(
        self, primary: pd.DataFrame, *, selected_source: str, providers: list[Any], symbol: str,
        start_date: date, end_date: date, attempts: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        if not self.cross_validate_market or not providers:
            return {"status": "skipped", "primary_source": selected_source, "secondary_sources": [], "details": [], "warnings": ["没有执行备用资金流向交叉验证"]}, [], attempts
        details: list[dict[str, Any]] = []
        secondary_audits: list[dict[str, Any]] = []
        for provider in providers:
            provider_name = getattr(provider, "name", type(provider).__name__)
            try:
                raw_data, retries = self._call_with_retry(lambda provider=provider: provider.fund_flow(symbol, start_date, end_date))
                if self._is_empty_result(raw_data):
                    raise DataProviderError(f"{provider_name} 返回空数据")
                retrieved_at = datetime.now(timezone.utc)
                normalized = normalize_fund_flow(raw_data, symbol=symbol, source=provider_name, retrieved_at=retrieved_at, start_date=start_date, end_date=end_date, data_version=getattr(provider, "data_version", None))
                quality = validate_fund_flow(normalized, rejected_data=normalized.attrs.get("rejected_data"), input_rows=normalized.attrs.get("input_rows"), warnings=normalized.attrs.get("normalization_warnings", []), column_mapping=normalized.attrs.get("column_mapping", {}))
                if quality.data.empty:
                    raise DataProviderError("备用资金流向质量校验后没有有效记录")
                comparison = compare_fund_flow(primary, quality.data, primary_source=selected_source, secondary_source=provider_name)
                details.append(comparison)
                secondary_audits.append({"source": provider_name, "status": comparison.get("status", "unavailable"), "raw_data": raw_data, "retrieved_at": retrieved_at, "data_version": getattr(provider, "data_version", None), "quality_report": quality.report, "comparison": comparison})
                attempts.append({"provider": provider_name, "role": "secondary_validation", "ok": True, "retries": retries, "quality_status": quality.report.get("status")})
            except Exception as exc:  # noqa: BLE001
                warning = f"备用源 {provider_name} 资金流向交叉验证不可用: {exc}"
                detail = {"status": "unavailable", "primary_source": selected_source, "secondary_source": provider_name, "common_rows": 0, "metrics": {}, "warnings": [warning]}
                details.append(detail)
                secondary_audits.append({"source": provider_name, "status": "unavailable", "raw_data": None, "retrieved_at": datetime.now(timezone.utc), "data_version": getattr(provider, "data_version", None), "quality_report": None, "comparison": detail, "error": str(exc)})
                attempts.append({"provider": provider_name, "role": "secondary_validation", "ok": False, "error": str(exc), "retryable": self._is_retryable(exc)})
        statuses = {item.get("status") for item in details}
        status = "mismatch" if "mismatch" in statuses else "matched" if "matched" in statuses else "unavailable"
        warnings = list(dict.fromkeys(warning for item in details for warning in item.get("warnings", [])))
        return {"status": status, "primary_source": selected_source, "secondary_sources": [item.get("secondary_source") for item in details], "details": details, "warnings": warnings}, secondary_audits, attempts

    def _run_with_fallback(
        self,
        providers: list[Any],
        operation: Callable[[Any], Any],
        *,
        operation_name: str,
    ) -> dict[str, Any]:
        if not providers:
            raise DataProviderError(f"没有可用的数据源: {operation_name}")

        attempts: list[dict[str, Any]] = []
        for index, provider in enumerate(providers):
            provider_name = getattr(provider, "name", type(provider).__name__)
            try:
                data, retries = self._call_with_retry(lambda: operation(provider))
                if self._is_empty_result(data):
                    raise DataProviderError(f"{provider_name} 返回空数据")
                return {
                    "data": data,
                    "source": provider_name,
                    "degraded": index > 0,
                    "attempts": [*attempts, {"provider": provider_name, "ok": True, "retries": retries}],
                }
            except Exception as exc:  # noqa: BLE001
                attempts.append(
                    {
                        "provider": provider_name,
                        "ok": False,
                        "error": str(exc),
                        "retryable": self._is_retryable(exc),
                    }
                )

        details = "; ".join(f"{item['provider']}: {item.get('error', 'failed')}" for item in attempts)
        raise DataProviderError(f"{operation_name} 的所有数据源均失败: {details}")

    def _call_with_retry(self, operation: Callable[[], Any]) -> tuple[Any, int]:
        max_attempts = max(1, settings.request_max_retries + 1)
        for attempt in range(max_attempts):
            try:
                return operation(), attempt
            except Exception as exc:  # noqa: BLE001
                if attempt >= max_attempts - 1 or not self._is_retryable(exc):
                    raise
                self.sleep_fn(min(10.0, 0.5 * (2**attempt)))
        raise RuntimeError("unreachable")

    @staticmethod
    def _is_empty_result(data: Any) -> bool:
        if data is None:
            return True
        if hasattr(data, "empty"):
            return bool(data.empty)
        if isinstance(data, (list, tuple, set, str)):
            return len(data) == 0
        if isinstance(data, dict):
            if "results" in data:
                return not data.get("results")
            return len(data) == 0
        return False

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        message = str(exc).lower()
        markers = (
            "429",
            "rate limit",
            "too many",
            "频率",
            "频次",
            "timeout",
            "timed out",
            "connection",
            "temporarily",
            "remote end closed",
            "503",
            "502",
            "504",
        )
        return any(marker in message for marker in markers)

    @staticmethod
    def _default_market_providers() -> list[DataProvider]:
        providers: list[DataProvider] = []
        for provider_cls in (TushareProvider, AkshareProvider):
            try:
                providers.append(provider_cls())
            except DataProviderError:
                continue
        return providers

    @staticmethod
    def _default_news_providers() -> list[Any]:
        try:
            return [TavilyProvider()]
        except DataProviderError:
            return []


def _concat_frames(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    parts = [frame.copy() for frame in (left, right) if frame is not None and not frame.empty]
    if not parts:
        return pd.DataFrame(columns=[*left.columns, *right.columns])
    result = pd.concat(parts, ignore_index=True, sort=False)
    result.attrs = {}
    return result
