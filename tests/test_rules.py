import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner.config import Settings
from scanner.market_data import _bars_from_table, _foreign_column, _to_float
from scanner.models import Bar, Pick
from scanner.report import format_report
from scanner.rules import (
    classify,
    find_breakout,
    improving_margins,
    is_common_stock,
    latest_quarter_operating_cashflow,
    score_pick,
    three_month_yoys,
)


def _bars(closes: list[float], volumes: list[float]) -> list[Bar]:
    cursor = date(2026, 1, 5)
    bars: list[Bar] = []
    for close, volume in zip(closes, volumes):
        while cursor.weekday() >= 5:
            cursor += timedelta(days=1)
        bars.append(Bar(cursor, "2330", "台積電", "上市", close, volume))
        cursor += timedelta(days=1)
    return bars


class RuleTests(unittest.TestCase):
    def test_common_stock_filter(self) -> None:
        self.assertTrue(is_common_stock("2330"))
        self.assertFalse(is_common_stock("0050"))
        self.assertFalse(is_common_stock("2330A"))
        self.assertFalse(is_common_stock("00664R"))

    def test_fresh_breakout_with_volume(self) -> None:
        closes = [10.0] * 20 + [12.0]
        volumes = [1000.0] * 20 + [2000.0]
        bars = _bars(closes, volumes)
        hit = find_breakout(bars, [bar.date for bar in bars], Settings())
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.days_ago, 0)
        self.assertAlmostEqual(hit.volume_ratio, 2.0)

    def test_rejects_light_volume_and_stale_cross(self) -> None:
        settings = Settings()
        light = _bars([10.0] * 20 + [12.0], [1000.0] * 20 + [1400.0])
        self.assertIsNone(find_breakout(light, [bar.date for bar in light], settings))

        # 突破日有量，但已經是 4 個交易日之前（days_ago = 3，不含在近 3 日內）。
        stale = _bars(
            [10.0] * 20 + [12.0, 12.0, 12.0, 12.0],
            [1000.0] * 20 + [2000.0, 1000.0, 1000.0, 1000.0],
        )
        self.assertIsNone(find_breakout(stale, [bar.date for bar in stale], settings))

        # 突破在倒數第三根，days_ago = 2，仍算剛站上。
        fresh = _bars(
            [10.0] * 20 + [12.0, 12.0, 12.0],
            [1000.0] * 20 + [2000.0, 1000.0, 1000.0],
        )
        hit = find_breakout(fresh, [bar.date for bar in fresh], settings)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.days_ago, 2)

    def test_rejects_close_back_under_average(self) -> None:
        bars = _bars([10.0] * 20 + [12.0, 8.0], [1000.0] * 20 + [2000.0, 2000.0])
        self.assertIsNone(find_breakout(bars, [bar.date for bar in bars], Settings()))

    def test_revenue_requires_three_strict_gains(self) -> None:
        data = {
            (2024, 7): 100,
            (2024, 8): 100,
            (2024, 9): 100,
            (2025, 7): 130,
            (2025, 8): 140,
            (2025, 9): 150,
        }
        yoys = three_month_yoys(
            data,
            months=3,
            asof=date(2025, 10, 28),
            min_yoy=0.20,
            max_lag_months=2,
        )
        self.assertEqual(yoys, (0.30, 0.40, 0.50))

        exact = dict(data)
        exact[(2025, 9)] = 120
        self.assertIsNone(
            three_month_yoys(exact, months=3, asof=date(2025, 10, 28), min_yoy=0.20, max_lag_months=2)
        )
        self.assertIsNone(
            three_month_yoys(data, months=3, asof=date(2025, 12, 15), min_yoy=0.20, max_lag_months=2)
        )

    def test_margin_and_cashflow(self) -> None:
        series = [
            (date(2025, 3, 31), 0.20),
            (date(2025, 6, 30), 0.22),
            (date(2025, 9, 30), 0.25),
        ]
        self.assertEqual(
            improving_margins(series, improvements=2, asof=date(2025, 10, 28), stale_days=210),
            (0.20, 0.22, 0.25),
        )
        slipped = [(date(2025, 3, 31), 0.20), (date(2025, 6, 30), 0.25), (date(2025, 9, 30), 0.22)]
        self.assertIsNone(improving_margins(slipped, improvements=2, asof=date(2025, 10, 28), stale_days=210))

        points = [(date(2025, 3, 31), 100.0), (date(2025, 6, 30), 180.0)]
        self.assertEqual(
            latest_quarter_operating_cashflow(points, asof=date(2025, 8, 1), stale_days=210),
            80.0,
        )
        negative = [(date(2025, 3, 31), 100.0), (date(2025, 6, 30), 40.0)]
        self.assertIsNone(latest_quarter_operating_cashflow(negative, asof=date(2025, 8, 1), stale_days=210))

    def test_style_and_empty_report(self) -> None:
        self.assertEqual(
            classify(volume_ratio=2.5, days_ago=1, yoy_mean=0.4, margin_delta=0.02, volume_ratio_min=1.5),
            "短線爆發潛力、適合波段",
        )
        self.assertEqual(
            classify(volume_ratio=1.6, days_ago=2, yoy_mean=0.22, margin_delta=0.005, volume_ratio_min=1.5),
            "適合波段",
        )
        text = format_report(date(2026, 10, 28), [], 0, Settings())
        self.assertIn("本日無推薦", text)

        pick = Pick(
            stock_id="2330",
            name="台積電",
            market="上市",
            close=1045,
            change_pct=0.018,
            score=82.4,
            style="適合波段",
            reason="測試理由",
            revenue_yoys=(0.3, 0.28, 0.25),
            gross_margins=(0.54, 0.55, 0.57),
            operating_cashflow=1.2e11,
            foreign_net_shares=1_234_000,
            volume_ratio=1.8,
            extension=0.012,
            cross_date=date(2026, 10, 27),
            ma=1032,
        )
        listed = format_report(date(2026, 10, 28), [pick], 1, Settings())
        self.assertIn("2330 台積電", listed)
        self.assertNotIn("本日無推薦", listed)
        self.assertGreaterEqual(score_pick(
            yoys=pick.revenue_yoys,
            margins=pick.gross_margins,
            foreign_net_shares=pick.foreign_net_shares,
            avg_volume=1_000_000,
            foreign_lookback=10,
            volume_ratio=1.8,
            volume_ratio_min=1.5,
            extension=0.012,
            min_yoy=0.2,
        ), 0)

    def test_market_parsers(self) -> None:
        self.assertEqual(_to_float("1,234.50"), 1234.5)
        self.assertIsNone(_to_float("--"))
        table = {
            "fields": ["證券代號", "證券名稱", "成交股數", "收盤價"],
            "data": [["2330", "台積電", "1,000", "900"], ["0050", "元大台灣50", "9", "180"]],
        }
        bars = _bars_from_table(table, date(2026, 10, 28), "上市")
        self.assertEqual([bar.stock_id for bar in bars], ["2330"])
        foreign = _foreign_column(
            ["證券代號", "外陸資買賣超股數(不含外資自營商)"],
            [["2330", "12,000"], ["0050", "99"]],
            "證券代號",
            "外陸資買賣超股數(不含外資自營商)",
        )
        self.assertEqual(foreign, {"2330": 12000.0})


if __name__ == "__main__":
    unittest.main()
