"""選股流程共用的資料結構。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Bar:
    date: date
    stock_id: str
    name: str
    market: str
    close: float
    volume: float


@dataclass(frozen=True)
class Breakout:
    cross_date: date
    days_ago: int
    close: float
    previous_close: float
    ma: float
    extension: float
    volume_ratio: float
    avg_volume: float


@dataclass(frozen=True)
class Pick:
    stock_id: str
    name: str
    market: str
    close: float
    change_pct: float
    score: float
    style: str
    reason: str
    revenue_yoys: tuple[float, ...]
    gross_margins: tuple[float, ...]
    operating_cashflow: float
    foreign_net_shares: float
    volume_ratio: float
    extension: float
    cross_date: date
    ma: float
