"""台股盤後選股主程式：抓資料、套條件、推播前五名。"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

from scanner.config import Settings
from scanner.fundamentals import evaluate
from scanner.http_client import HttpClient
from scanner.market_data import (
    load_foreign_net,
    load_official_revenue_yoy,
    load_price_history,
    resolve_session,
)
from scanner.models import Bar, Breakout, Pick
from scanner.notify import send_message
from scanner.report import format_report
from scanner.rules import build_reason, classify, find_breakout, score_pick

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scanner")


@dataclass(frozen=True)
class Candidate:
    latest: Bar
    breakout: Breakout
    foreign_net_shares: float


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(override=False)
    try:
        return run()
    except Exception as exc:
        logger.exception("選股失敗")
        _notify_failure(exc)
        return 1


def run() -> int:
    settings = Settings.from_env()
    missing = _missing_telegram()
    if missing:
        raise RuntimeError("缺少 Telegram 設定：" + "、".join(missing) + "。請看 README 的 GitHub Secrets 步驟。")
    if not os.getenv("FINMIND_TOKEN", "").strip():
        logger.warning("沒有 FINMIND_TOKEN，將以 FinMind 免費匿名額度查財報，檔數一多可能被限制")

    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    mode = os.getenv("SCAN_MODE", "manual").strip() or "manual"
    client = HttpClient()
    session = resolve_session(client, today, mode, settings)
    if session is None:
        logger.info("今日非交易日，或收盤資料尚未公布，不推播")
        return 0

    market_dates, bars_by_id, price_notes = load_price_history(client, session, settings)
    foreign, foreign_notes = load_foreign_net(client, market_dates, settings)
    official_yoy = load_official_revenue_yoy(client)
    candidates, counts = _technical_candidates(bars_by_id, market_dates, foreign, official_yoy, settings)
    logger.info(
        "普通股 %s｜剛帶量站上月線 %s｜外資買超 %s｜最新月營收未達標而排除 %s｜進入財報檢查 %s",
        counts["universe"],
        counts["breakout"],
        counts["foreign"],
        counts["revenue_rejected"],
        len(candidates),
    )

    notes = [*price_notes, *foreign_notes]
    if len(candidates) > settings.max_fundamental_checks:
        notes.append(f"技術面候選較多，已依量能與外資買超先檢查前 {settings.max_fundamental_checks} 檔。")
        candidates = candidates[: settings.max_fundamental_checks]
    note = "".join(notes)

    picks = _apply_fundamentals(client, candidates, session, settings)
    picks.sort(key=lambda pick: (-pick.score, -pick.foreign_net_shares, pick.stock_id))
    top = picks[: settings.top_n]
    manual_note = ""
    if mode != "schedule" and session != today:
        manual_note = f"今天 {today.isoformat()} 尚無收盤資料，這份是最近交易日 {session.isoformat()} 的結果。"
    text = format_report(session, top, len(picks), settings, note=note, manual_note=manual_note)
    if top:
        frame = pd.DataFrame(
            [
                {
                    "代號": pick.stock_id,
                    "名稱": pick.name,
                    "市場": pick.market,
                    "評分": pick.score,
                    "型態": pick.style,
                    "收盤": pick.close,
                }
                for pick in top
            ]
        )
        logger.info("\n%s", frame.to_string(index=False))
    logger.info("符合全部條件 %s 檔，推播 %s 檔", len(picks), len(top))
    send_message(text)
    return 0


def _technical_candidates(
    bars_by_id: dict[str, list[Bar]],
    market_dates: list,
    foreign: dict[str, float],
    official_yoy: dict[str, float],
    settings: Settings,
) -> tuple[list[Candidate], dict[str, int]]:
    counts = {"universe": len(bars_by_id), "breakout": 0, "foreign": 0, "revenue_rejected": 0}
    candidates: list[Candidate] = []
    for stock_id, bars in bars_by_id.items():
        breakout = find_breakout(bars, market_dates, settings)
        if breakout is None:
            continue
        counts["breakout"] += 1
        net = foreign.get(stock_id, 0.0)
        if net <= 0:
            continue
        counts["foreign"] += 1
        yoy = official_yoy.get(stock_id)
        if yoy is not None and yoy <= settings.revenue_yoy_min * 100:
            counts["revenue_rejected"] += 1
            continue
        latest = max(bars, key=lambda bar: bar.date)
        candidates.append(Candidate(latest=latest, breakout=breakout, foreign_net_shares=net))
    candidates.sort(
        key=lambda item: (item.breakout.volume_ratio, item.foreign_net_shares),
        reverse=True,
    )
    return candidates, counts


def _apply_fundamentals(client: HttpClient, candidates: list[Candidate], session, settings: Settings) -> list[Pick]:
    picks: list[Pick] = []
    failures = 0
    api_errors = 0
    for candidate in candidates:
        stock_id = candidate.latest.stock_id
        try:
            fundamentals = evaluate(client, stock_id, session, settings)
        except RuntimeError as exc:
            failures += 1
            api_errors += 1
            logger.warning("%s 財報取得失敗：%s", stock_id, exc)
            if failures >= 5:
                raise RuntimeError("FinMind 連續失敗，已停止。請確認 FINMIND_TOKEN 與當小時額度。") from exc
            continue
        failures = 0
        if fundamentals is None:
            continue
        yoy_mean = sum(fundamentals.yoys) / len(fundamentals.yoys)
        margin_delta = fundamentals.margins[-1] - fundamentals.margins[0]
        style = classify(
            volume_ratio=candidate.breakout.volume_ratio,
            days_ago=candidate.breakout.days_ago,
            yoy_mean=yoy_mean,
            margin_delta=margin_delta,
            volume_ratio_min=settings.volume_ratio_min,
        )
        previous = candidate.breakout.previous_close
        change_pct = 0.0 if previous == 0 else candidate.latest.close / previous - 1
        picks.append(
            Pick(
                stock_id=stock_id,
                name=candidate.latest.name,
                market=candidate.latest.market,
                close=candidate.latest.close,
                change_pct=change_pct,
                score=score_pick(
                    yoys=fundamentals.yoys,
                    margins=fundamentals.margins,
                    foreign_net_shares=candidate.foreign_net_shares,
                    avg_volume=candidate.breakout.avg_volume,
                    foreign_lookback=settings.foreign_lookback,
                    volume_ratio=candidate.breakout.volume_ratio,
                    volume_ratio_min=settings.volume_ratio_min,
                    extension=candidate.breakout.extension,
                    min_yoy=settings.revenue_yoy_min,
                ),
                style=style,
                reason=build_reason(style),
                revenue_yoys=fundamentals.yoys,
                gross_margins=fundamentals.margins,
                operating_cashflow=fundamentals.operating_cashflow,
                foreign_net_shares=candidate.foreign_net_shares,
                volume_ratio=candidate.breakout.volume_ratio,
                extension=candidate.breakout.extension,
                cross_date=candidate.breakout.cross_date,
                ma=candidate.breakout.ma,
            )
        )
    if candidates and api_errors == len(candidates):
        raise RuntimeError("財報全部查詢失敗，沒有產生推薦。請確認 FINMIND_TOKEN 與當小時額度。")
    return picks


def _missing_telegram() -> list[str]:
    if os.getenv("DRY_RUN") == "1":
        return []
    return [name for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not os.getenv(name, "").strip()]


def _notify_failure(exc: Exception) -> None:
    if _missing_telegram():
        return
    try:
        send_message(f"台股選股腳本執行失敗：{exc}")
    except Exception:
        logger.exception("失敗通知也送不出去")


if __name__ == "__main__":
    raise SystemExit(main())
