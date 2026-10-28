"""把選股結果排成 Telegram 可直接讀的純文字。"""

from __future__ import annotations

from datetime import date

from scanner.config import Settings
from scanner.models import Pick


def condition_line(settings: Settings) -> str:
    return (
        f"條件：近{settings.revenue_months}月營收年增>{_percent(settings.revenue_yoy_min)}、"
        f"毛利率連續{settings.margin_improvements}季改善、營業現金流為正、"
        f"外資近{settings.foreign_lookback}日買超、"
        f"近{settings.cross_lookback}個交易日內量能≥{settings.volume_ratio_min:g}倍站上{settings.ma_window}日均線。"
    )


def format_report(
    session: date,
    picks: list[Pick],
    total_matched: int,
    settings: Settings,
    note: str = "",
    manual_note: str = "",
) -> str:
    lines = [f"台股盤後選股 {session.isoformat()}"]
    if manual_note:
        lines.append(manual_note)
    if not picks:
        lines.append("本日無推薦")
    else:
        lines.append(f"符合全部條件 {total_matched} 檔，以下為評分前 {len(picks)} 名。")
    if note:
        lines.append(note)

    for index, pick in enumerate(picks, start=1):
        sign = "+" if pick.change_pct >= 0 else ""
        yoy_text = "、".join(f"{value * 100:.1f}%" for value in pick.revenue_yoys)
        margin_text = " → ".join(f"{value * 100:.1f}%" for value in pick.gross_margins)
        suffix = ".TW" if pick.market == "上市" else ".TWO"
        lines.append("")
        lines.append(
            "\n".join(
                [
                    f"{index}. {pick.stock_id} {pick.name}〔{pick.market}〕",
                    f"收盤 {pick.close:,.2f}（{sign}{pick.change_pct * 100:.2f}%）  評分 {pick.score:.1f}",
                    f"型態：{pick.style}",
                    f"近{settings.revenue_months}月營收年增：{yoy_text}",
                    f"毛利率：{margin_text}",
                    f"營業現金流（最新單季）：{pick.operating_cashflow / 1e8:+,.1f} 億",
                    f"外資近{settings.foreign_lookback}日買超：{pick.foreign_net_shares / 1000:,.1f} 張",
                    (
                        f"技術：{pick.cross_date.isoformat()} 量能 {pick.volume_ratio:.1f} 倍站上月線，"
                        f"現價距月線 {pick.extension * 100:+.1f}%（月線 {pick.ma:,.2f}）"
                    ),
                    f"理由：{pick.reason}",
                    f"https://tw.stock.yahoo.com/quote/{pick.stock_id}{suffix}",
                ]
            )
        )

    lines.append(condition_line(settings))
    lines.append("資料來源：臺灣證券交易所、證券櫃檯買賣中心、FinMind。此為條件篩選，不是投資建議。")
    return "\n".join(lines)


def _percent(value: float) -> str:
    return f"{value * 100:g}%"
