"""用 FinMind 補官方快照沒有的歷史：近三個月營收、季毛利率、營業現金流。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta

from scanner.config import Settings
from scanner.http_client import HttpClient
from scanner.rules import improving_margins, latest_quarter_operating_cashflow, three_month_yoys

logger = logging.getLogger(__name__)
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
_skip_token = False


@dataclass(frozen=True)
class Fundamentals:
    yoys: tuple[float, ...]
    margins: tuple[float, ...]
    operating_cashflow: float


def evaluate(client: HttpClient, stock_id: str, asof: date, settings: Settings) -> Fundamentals | None:
    revenue_start = (asof - timedelta(days=500)).isoformat()
    statement_start = (asof - timedelta(days=900)).isoformat()
    revenue_rows = _fetch(client, "TaiwanStockMonthRevenue", stock_id, revenue_start, settings)
    statement_rows = _fetch(client, "TaiwanStockFinancialStatements", stock_id, statement_start, settings)
    cash_rows = _fetch(client, "TaiwanStockCashFlowsStatement", stock_id, statement_start, settings)

    yoys = three_month_yoys(
        _revenue_map(revenue_rows),
        months=settings.revenue_months,
        asof=asof,
        min_yoy=settings.revenue_yoy_min,
        max_lag_months=settings.stale_revenue_months,
    )
    margins = improving_margins(
        _margin_series(statement_rows),
        improvements=settings.margin_improvements,
        asof=asof,
        stale_days=settings.stale_statement_days,
    )
    cashflow = latest_quarter_operating_cashflow(
        _cashflow_points(cash_rows),
        asof=asof,
        stale_days=settings.stale_statement_days,
    )
    if yoys is None or margins is None or cashflow is None:
        return None
    return Fundamentals(yoys=yoys, margins=margins, operating_cashflow=cashflow)


def _fetch(client: HttpClient, dataset: str, stock_id: str, start_date: str, settings: Settings) -> list[dict]:
    global _skip_token
    token = "" if _skip_token else os.getenv("FINMIND_TOKEN", "").strip()
    try:
        return _fetch_once(client, dataset, stock_id, start_date, settings, token)
    except RuntimeError as exc:
        if not token or "Token is illegal" not in str(exc):
            raise
        _skip_token = True
        logger.warning("FinMind token 無法使用，改以匿名額度查詢")
        return _fetch_once(client, dataset, stock_id, start_date, settings, "")


def _fetch_once(
    client: HttpClient,
    dataset: str,
    stock_id: str,
    start_date: str,
    settings: Settings,
    token: str,
) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = client.get_json(
        FINMIND_URL,
        params={"dataset": dataset, "data_id": stock_id, "start_date": start_date},
        headers=headers,
        pause=settings.finmind_pause,
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise RuntimeError(f"FinMind {dataset} {stock_id} 沒有 data")
    return data


def _revenue_map(rows: list[dict]) -> dict[tuple[int, int], float]:
    mapping: dict[tuple[int, int], float] = {}
    for row in sorted(rows, key=lambda item: str(item.get("date") or "")):
        year = _int(row.get("revenue_year"))
        month = _int(row.get("revenue_month"))
        revenue = _float(row.get("revenue"))
        if year is None or month is None or revenue is None:
            continue
        if year < 1911:
            year += 1911
        if not 1 <= month <= 12:
            continue
        mapping[(year, month)] = revenue
    return mapping


def _margin_series(rows: list[dict]) -> list[tuple[date, float]]:
    revenue: dict[date, float] = {}
    gross: dict[date, float] = {}
    for row in rows:
        point = _date(row.get("date"))
        value = _float(row.get("value"))
        if point is None or value is None:
            continue
        kind = str(row.get("type") or "")
        if kind == "Revenue":
            revenue[point] = value
        elif kind == "GrossProfit":
            gross[point] = value
    series: list[tuple[date, float]] = []
    for point, sales in revenue.items():
        profit = gross.get(point)
        if profit is None or sales <= 0:
            continue
        series.append((point, profit / sales))
    return series


def _cashflow_points(rows: list[dict]) -> list[tuple[date, float]]:
    points: dict[date, float] = {}
    for row in rows:
        if str(row.get("type") or "") != "CashFlowsFromOperatingActivities":
            continue
        point = _date(row.get("date"))
        value = _float(row.get("value"))
        if point is None or value is None:
            continue
        points[point] = value
    return list(points.items())


def _date(value: object) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _int(value: object) -> int | None:
    number = _float(value)
    if number is None:
        return None
    return int(number)


def _float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
