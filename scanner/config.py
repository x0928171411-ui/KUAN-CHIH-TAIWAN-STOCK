"""可調參數。環境變數會覆寫預設值，方便在 GitHub Actions 裡改條件而不改程式。"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: float | int) -> float | int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return type(default)(raw.strip())


@dataclass(frozen=True)
class Settings:
    # 近 N 個月營收年增率都必須大於這個比例。0.20 = 20%。
    revenue_yoy_min: float = 0.20
    revenue_months: int = 3
    # 連續改善的季數。2 表示最近三段季毛利率一次比一次高。
    margin_improvements: int = 2
    foreign_lookback: int = 10
    ma_window: int = 20
    # 突破發生在最近幾個交易日內，且最新收盤仍站在均線上。
    cross_lookback: int = 3
    # 突破當日成交量 / 前 ma_window 日均量。
    volume_ratio_min: float = 1.5
    top_n: int = 5
    # 要抓幾個交易日的價格，必須涵蓋均線與「剛突破」的回看。
    history_sessions: int = 32
    max_calendar_days: int = 80
    # 財報是一檔一查。先用量能與外資排序，避免免費 API 額度不夠。
    max_fundamental_checks: int = 60
    twse_pause: float = 1.0
    tpex_pause: float = 0.4
    finmind_pause: float = 0.25
    # 最新月營收最多落後目前月份幾個月，超過就視為資料太舊。
    stale_revenue_months: int = 2
    stale_statement_days: int = 210

    @classmethod
    def from_env(cls) -> Settings:
        base = cls()
        settings = cls(
            revenue_yoy_min=_env("REVENUE_YOY_MIN", base.revenue_yoy_min),
            revenue_months=_env("REVENUE_MONTHS", base.revenue_months),
            margin_improvements=_env("MARGIN_IMPROVEMENTS", base.margin_improvements),
            foreign_lookback=_env("FOREIGN_LOOKBACK", base.foreign_lookback),
            ma_window=_env("MA_WINDOW", base.ma_window),
            cross_lookback=_env("CROSS_LOOKBACK", base.cross_lookback),
            volume_ratio_min=_env("VOLUME_RATIO_MIN", base.volume_ratio_min),
            top_n=_env("TOP_N", base.top_n),
            history_sessions=_env("HISTORY_SESSIONS", base.history_sessions),
            max_calendar_days=_env("MAX_CALENDAR_DAYS", base.max_calendar_days),
            max_fundamental_checks=_env("MAX_FUNDAMENTAL_CHECKS", base.max_fundamental_checks),
            twse_pause=_env("TWSE_PAUSE", base.twse_pause),
            tpex_pause=_env("TPEX_PAUSE", base.tpex_pause),
            finmind_pause=_env("FINMIND_PAUSE", base.finmind_pause),
            stale_revenue_months=_env("STALE_REVENUE_MONTHS", base.stale_revenue_months),
            stale_statement_days=_env("STALE_STATEMENT_DAYS", base.stale_statement_days),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.revenue_months < 1:
            raise ValueError("REVENUE_MONTHS 至少為 1")
        if self.margin_improvements < 1:
            raise ValueError("MARGIN_IMPROVEMENTS 至少為 1")
        if self.foreign_lookback < 1:
            raise ValueError("FOREIGN_LOOKBACK 至少為 1")
        if self.ma_window < 2:
            raise ValueError("MA_WINDOW 至少為 2")
        if self.cross_lookback < 1:
            raise ValueError("CROSS_LOOKBACK 至少為 1")
        if self.volume_ratio_min <= 0:
            raise ValueError("VOLUME_RATIO_MIN 必須大於 0")
        if self.top_n < 1:
            raise ValueError("TOP_N 至少為 1")
        if self.history_sessions < self.ma_window + self.cross_lookback:
            raise ValueError("HISTORY_SESSIONS 至少要等於 MA_WINDOW + CROSS_LOOKBACK")
        if self.history_sessions < self.foreign_lookback:
            raise ValueError("HISTORY_SESSIONS 不能小於 FOREIGN_LOOKBACK")
        if self.max_fundamental_checks < 1:
            raise ValueError("MAX_FUNDAMENTAL_CHECKS 至少為 1")
