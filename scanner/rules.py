"""純計算的篩選規則，不碰網路，方便單獨測試。"""

from __future__ import annotations

import re
from datetime import date

from scanner.config import Settings
from scanner.models import Bar, Breakout

_COMMON_STOCK = re.compile(r"^[1-9]\d{3}$")

# 分數權重加總為 1。想改「什麼叫強勢」，調這裡。
WEIGHT_REVENUE = 0.30
WEIGHT_MARGIN = 0.20
WEIGHT_FOREIGN = 0.20
WEIGHT_VOLUME = 0.15
WEIGHT_EXTENSION = 0.15


def is_common_stock(stock_id: str) -> bool:
    """四碼普通股。排除 00 開頭的 ETF、以及帶字母的特別股與權證。"""
    return _COMMON_STOCK.fullmatch(stock_id.strip()) is not None


def find_breakout(bars: list[Bar], market_dates: list[date], settings: Settings) -> Breakout | None:
    """剛帶量站上均線。

    突破日定義：當天收盤高於均線，前一天收盤不高於前一天的均線。
    「剛」：突破日落在最近 cross_lookback 個交易日內，而且最新收盤仍在均線之上。
    「帶量」：突破當日成交量至少是前 ma_window 日均量的 volume_ratio_min 倍。
    """
    if not bars or not market_dates:
        return None
    ordered = _dedupe(bars)
    if ordered[-1].date != market_dates[-1]:
        return None
    window = settings.ma_window
    if len(ordered) < window + 1:
        return None

    closes = [bar.close for bar in ordered]
    volumes = [bar.volume for bar in ordered]
    averages = _moving_averages(closes, window)

    last_cross: int | None = None
    for index in range(window, len(ordered)):
        previous_ma = averages[index - 1]
        current_ma = averages[index]
        if previous_ma is None or current_ma is None:
            continue
        crossed = closes[index] > current_ma and closes[index - 1] <= previous_ma
        if crossed:
            last_cross = index
    if last_cross is None:
        return None

    latest_ma = averages[-1]
    if latest_ma is None or closes[-1] <= latest_ma:
        return None

    cross_date = ordered[last_cross].date
    try:
        days_ago = len(market_dates) - 1 - market_dates.index(cross_date)
    except ValueError:
        return None
    if days_ago < 0 or days_ago >= settings.cross_lookback:
        return None

    prior = volumes[last_cross - window : last_cross]
    avg_volume = sum(prior) / len(prior)
    if avg_volume <= 0:
        return None
    volume_ratio = volumes[last_cross] / avg_volume
    if volume_ratio < settings.volume_ratio_min:
        return None

    return Breakout(
        cross_date=cross_date,
        days_ago=days_ago,
        close=closes[-1],
        previous_close=closes[-2],
        ma=latest_ma,
        extension=closes[-1] / latest_ma - 1,
        volume_ratio=volume_ratio,
        avg_volume=avg_volume,
    )


def three_month_yoys(
    month_to_revenue: dict[tuple[int, int], float],
    *,
    months: int,
    asof: date,
    min_yoy: float,
    max_lag_months: int,
) -> tuple[float, ...] | None:
    """最近連續 N 個月的年增率，由舊到新。任一月沒過門檻、缺去年同月、或資料太舊就回傳 None。"""
    usable = {
        key: value
        for key, value in month_to_revenue.items()
        if (key[0], key[1]) <= (asof.year, asof.month) and value is not None
    }
    if not usable:
        return None
    latest_year, latest_month = max(usable)
    lag = (asof.year - latest_year) * 12 + (asof.month - latest_month)
    if lag > max_lag_months:
        return None

    needed = _recent_months(latest_year, latest_month, months)
    yoys: list[float] = []
    for year, month in needed:
        current = usable.get((year, month))
        base = usable.get((year - 1, month))
        if current is None or base is None or base <= 0:
            return None
        yoy = (current - base) / base
        if yoy <= min_yoy:
            return None
        yoys.append(yoy)
    return tuple(yoys)


def improving_margins(
    series: list[tuple[date, float]],
    *,
    improvements: int,
    asof: date,
    stale_days: int,
) -> tuple[float, ...] | None:
    """最近一段季毛利率要連續上升。每段間隔必須是一個季度（3 個月）。"""
    if len(series) < improvements + 1:
        return None
    ordered = sorted(series, key=lambda item: item[0])
    tail = ordered[-(improvements + 1) :]
    if (asof - tail[-1][0]).days > stale_days:
        return None
    for previous, current in zip(tail, tail[1:]):
        if _month_gap(previous[0], current[0]) != 3 or current[1] <= previous[1]:
            return None
    return tuple(item[1] for item in tail)


def latest_quarter_operating_cashflow(
    ytd_points: list[tuple[date, float]],
    *,
    asof: date,
    stale_days: int,
) -> float | None:
    """現金流量表是年初至今累計。同一年度相鄰兩季相減，得到最新單季營業現金流。必須為正。"""
    if not ytd_points:
        return None
    ordered = sorted(ytd_points, key=lambda item: item[0])
    latest_date, latest_ytd = ordered[-1]
    if (asof - latest_date).days > stale_days:
        return None
    previous_ytd = None
    for point_date, value in ordered[:-1]:
        if point_date.year == latest_date.year:
            previous_ytd = value
    quarterly = latest_ytd if previous_ytd is None else latest_ytd - previous_ytd
    if quarterly <= 0:
        return None
    return quarterly


def score_pick(
    *,
    yoys: tuple[float, ...],
    margins: tuple[float, ...],
    foreign_net_shares: float,
    avg_volume: float,
    foreign_lookback: int,
    volume_ratio: float,
    volume_ratio_min: float,
    extension: float,
    min_yoy: float,
) -> float:
    mean_yoy = sum(yoys) / len(yoys)
    revenue_score = _clamp((mean_yoy - min_yoy) / 0.80)
    margin_score = _clamp((margins[-1] - margins[0]) / 0.05)
    traded = avg_volume * foreign_lookback
    foreign_score = 0.0 if traded <= 0 else _clamp((foreign_net_shares / traded) / 0.15)
    volume_score = _clamp((volume_ratio - volume_ratio_min) / 1.5)
    extension_score = _extension_score(extension)
    total = (
        WEIGHT_REVENUE * revenue_score
        + WEIGHT_MARGIN * margin_score
        + WEIGHT_FOREIGN * foreign_score
        + WEIGHT_VOLUME * volume_score
        + WEIGHT_EXTENSION * extension_score
    )
    return round(total * 100, 1)


def classify(
    *,
    volume_ratio: float,
    days_ago: int,
    yoy_mean: float,
    margin_delta: float,
    volume_ratio_min: float,
) -> str:
    burst = volume_ratio >= 2.0 or (days_ago == 0 and volume_ratio >= volume_ratio_min)
    swing = yoy_mean >= 0.30 or margin_delta >= 0.015
    if burst and swing:
        return "短線爆發潛力、適合波段"
    if burst:
        return "短線爆發潛力"
    return "適合波段"


def build_reason(style: str) -> str:
    if style == "短線爆發潛力、適合波段":
        return "量能與外資同時轉強，短線有爆發空間；營收與毛利率連續改善，也適合波段持有。"
    if style == "短線爆發潛力":
        return "股價剛帶量站上月線，外資近十日站在買方，短線爆發潛力較高。"
    return "營收與毛利率連續改善、現金流為正，股價剛站回月線，較適合波段布局。"


def _dedupe(bars: list[Bar]) -> list[Bar]:
    by_date: dict[date, Bar] = {}
    for bar in bars:
        by_date[bar.date] = bar
    return [by_date[key] for key in sorted(by_date)]


def _moving_averages(values: list[float], window: int) -> list[float | None]:
    averages: list[float | None] = [None] * len(values)
    rolling = 0.0
    for index, value in enumerate(values):
        rolling += value
        if index >= window:
            rolling -= values[index - window]
        if index >= window - 1:
            averages[index] = rolling / window
    return averages


def _recent_months(year: int, month: int, count: int) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    cursor_year, cursor_month = year, month
    for _ in range(count):
        points.append((cursor_year, cursor_month))
        cursor_month -= 1
        if cursor_month == 0:
            cursor_month = 12
            cursor_year -= 1
    points.reverse()
    return points


def _month_gap(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def _extension_score(extension: float) -> float:
    """剛站上、還沒乖離太多的分數最高。離月線 0%～3% 最好。"""
    if extension <= 0:
        return 0.0
    if extension <= 0.03:
        return 0.7 + 0.3 * (extension / 0.03)
    if extension <= 0.08:
        return 1 - (extension - 0.03) / 0.05 * 0.7
    return max(0.0, 0.3 - (extension - 0.08) / 0.07 * 0.3)


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)
