"""上市與上櫃的日成交、三大法人、最新月營收。這些端點一次回全市場。"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date, timedelta

from scanner.config import Settings
from scanner.http_client import HttpClient
from scanner.models import Bar
from scanner.rules import is_common_stock

logger = logging.getLogger(__name__)

TWSE_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_QUOTE_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
TPEX_INST_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
TPEX_REVENUE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"

TWSE_HEADERS = {"Referer": "https://www.twse.com.tw/zh/trading/historical/mi-index.html"}
TPEX_HEADERS = {"Referer": "https://www.tpex.org.tw/"}
YOY_FIELD = "營業收入-去年同月增減(%)"


def resolve_session(client: HttpClient, today: date, mode: str, settings: Settings) -> date | None:
    """排程只分析今天的收盤。手動執行時，若今天還沒有資料，改看最近一個交易日。"""
    if _has_session(client, today, settings):
        return today
    if mode == "schedule":
        if today.weekday() >= 5:
            return None
        for _ in range(2):
            logger.info("今天是交易日但收盤資料還沒齊，20 秒後再查一次")
            time.sleep(20)
            if _has_session(client, today, settings):
                return today
        return None

    cursor = today
    for _ in range(10):
        if cursor.weekday() < 5 and _has_session(client, cursor, settings):
            return cursor
        cursor -= timedelta(days=1)
    return None


def load_price_history(
    client: HttpClient,
    session: date,
    settings: Settings,
) -> tuple[list[date], dict[str, list[Bar]], list[str]]:
    found: list[date] = []
    grouped: dict[str, list[Bar]] = defaultdict(list)
    listed_errors = 0
    otc_errors = 0
    cursor = session
    scanned = 0
    while len(found) < settings.history_sessions and scanned < settings.max_calendar_days:
        scanned += 1
        if cursor.weekday() >= 5:
            cursor -= timedelta(days=1)
            continue
        listed, otc, listed_err, otc_err = _load_day_prices(client, cursor, settings)
        listed_errors += int(listed_err)
        otc_errors += int(otc_err)
        if listed_err and otc_err:
            raise RuntimeError(f"{cursor.isoformat()} 上市與上櫃行情都無法取得")
        if listed or otc:
            found.append(cursor)
            for bar in listed + otc:
                grouped[bar.stock_id].append(bar)
            if listed_err or otc_err or not listed or not otc:
                logger.warning("%s 行情不完整（上市 %s 檔、上櫃 %s 檔）", cursor, len(listed), len(otc))
        cursor -= timedelta(days=1)

    if len(found) < settings.history_sessions:
        raise RuntimeError(
            f"只拿到 {len(found)} 個交易日，少於需要的 {settings.history_sessions} 日。"
            "可能是證交所或櫃買暫時擋住這個 IP。"
        )
    market_dates = sorted(found)
    logger.info("價格歷史 %s 個交易日（%s～%s），普通股 %s 檔", len(market_dates), market_dates[0], market_dates[-1], len(grouped))
    warnings = _market_warnings("行情", listed_errors, otc_errors)
    return market_dates, grouped, warnings


def load_foreign_net(
    client: HttpClient,
    market_dates: list[date],
    settings: Settings,
) -> tuple[dict[str, float], list[str]]:
    """近 N 個交易日外陸資買賣超股數（不含外資自營商）的合計。"""
    dates = market_dates[-settings.foreign_lookback :]
    totals: dict[str, float] = defaultdict(float)
    failed = 0
    listed_errors = 0
    otc_errors = 0
    for day in dates:
        listed, listed_err = _attempt(lambda: _twse_foreign(client, day, settings), f"上市三大法人 {day}")
        otc, otc_err = _attempt(lambda: _tpex_foreign(client, day, settings), f"上櫃三大法人 {day}")
        listed = listed or {}
        otc = otc or {}
        listed_errors += int(listed_err)
        otc_errors += int(otc_err)
        if not listed and not otc:
            failed += 1
            logger.warning("三大法人 %s 沒有可用資料", day)
            continue
        for stock_id, shares in {**listed, **otc}.items():
            totals[stock_id] += shares
    if failed > 2:
        raise RuntimeError(f"近 {len(dates)} 日有 {failed} 日抓不到三大法人，為避免誤判已停止")
    logger.info("外資買賣超已累計 %s 個交易日", len(dates) - failed)
    return dict(totals), _market_warnings("三大法人", listed_errors, otc_errors)


def load_official_revenue_yoy(client: HttpClient) -> dict[str, float]:
    """最新一個月的營收年增率（百分點，20 代表 20%）。只當進財報前的快速門檻。"""
    result: dict[str, float] = {}
    for market, url in (("上市", TWSE_REVENUE_URL), ("上櫃", TPEX_REVENUE_URL)):
        rows = client.get_json(url, pause=0.2)
        if not isinstance(rows, list):
            raise RuntimeError(f"{market}月營收格式不符")
        kept = 0
        for row in rows:
            stock_id = str(row.get("公司代號", "")).strip()
            if not is_common_stock(stock_id):
                continue
            yoy = _to_float(row.get(YOY_FIELD))
            if yoy is None:
                continue
            result[stock_id] = yoy
            kept += 1
        logger.info("%s最新月營收 %s 檔", market, kept)
    return result


def _has_session(client: HttpClient, day: date, settings: Settings) -> bool:
    listed, otc, listed_err, otc_err = _load_day_prices(client, day, settings)
    if listed_err and otc_err:
        raise RuntimeError(f"{day.isoformat()} 上市與上櫃行情都無法取得")
    return bool(listed or otc)


def _load_day_prices(
    client: HttpClient,
    day: date,
    settings: Settings,
) -> tuple[list[Bar], list[Bar], bool, bool]:
    listed, listed_err = _attempt(lambda: _twse_prices(client, day, settings), f"上市行情 {day}")
    otc, otc_err = _attempt(lambda: _tpex_prices(client, day, settings), f"上櫃行情 {day}")
    return listed or [], otc or [], listed_err, otc_err


def _attempt(func, label: str):
    try:
        return func(), False
    except RuntimeError as exc:
        logger.warning("%s 失敗：%s", label, exc)
        return None, True


def _market_warnings(kind: str, listed_errors: int, otc_errors: int) -> list[str]:
    warnings: list[str] = []
    if listed_errors > 2:
        warnings.append(f"上市{kind}有 {listed_errors} 日沒取到，本次上市結果可能偏少。")
    if otc_errors > 2:
        warnings.append(f"上櫃{kind}有 {otc_errors} 日沒取到，本次上櫃結果可能偏少。")
    return warnings


def _twse_prices(client: HttpClient, day: date, settings: Settings) -> list[Bar]:
    payload = client.get_json(
        TWSE_INDEX_URL,
        params={"date": day.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
        headers=TWSE_HEADERS,
        pause=settings.twse_pause,
    )
    bars: list[Bar] = []
    for table in _tables(payload, "收盤價", "證券代號"):
        bars.extend(_bars_from_table(table, day, "上市"))
    return bars


def _tpex_prices(client: HttpClient, day: date, settings: Settings) -> list[Bar]:
    payload = client.get_json(
        TPEX_QUOTE_URL,
        params={"date": day.strftime("%Y/%m/%d"), "id": "", "response": "json"},
        headers=TPEX_HEADERS,
        pause=settings.tpex_pause,
    )
    if str(payload.get("date", "")) != day.strftime("%Y%m%d"):
        return []
    bars: list[Bar] = []
    for table in _tables(payload, "收盤", "代號"):
        bars.extend(_bars_from_table(table, day, "上櫃"))
    return bars


def _twse_foreign(client: HttpClient, day: date, settings: Settings) -> dict[str, float]:
    payload = client.get_json(
        TWSE_T86_URL,
        params={"date": day.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"},
        headers=TWSE_HEADERS,
        pause=settings.twse_pause,
    )
    if payload.get("stat") != "OK":
        return {}
    return _foreign_column(payload.get("fields") or [], payload.get("data") or [], "證券代號", "外陸資買賣超股數(不含外資自營商)")


def _tpex_foreign(client: HttpClient, day: date, settings: Settings) -> dict[str, float]:
    payload = client.get_json(
        TPEX_INST_URL,
        params={"type": "Daily", "date": day.strftime("%Y/%m/%d"), "id": "", "response": "json"},
        headers=TPEX_HEADERS,
        pause=settings.tpex_pause,
    )
    if str(payload.get("date", "")) != day.strftime("%Y%m%d"):
        return {}
    table = _quote_table(payload, "買賣超股數", "代號")
    if table is None:
        return {}
    # 櫃買欄位名稱重複。第一組買賣超是「外資及陸資(不含外資自營商)」，對齊上市 T86。
    fields = table.get("fields") or []
    if len(fields) < 5 or fields[4] != "買賣超股數":
        raise RuntimeError("櫃買三大法人欄位順序改變，已停止以免算錯外資")
    return _foreign_by_index(fields, table.get("data") or [], code_index=0, net_index=4)


def _tables(payload: dict, price_field: str, code_field: str) -> list[dict]:
    found: list[dict] = []
    for table in payload.get("tables") or []:
        fields = table.get("fields") or []
        if price_field in fields and code_field in fields and table.get("data"):
            found.append(table)
    return found


def _quote_table(payload: dict, price_field: str, code_field: str) -> dict | None:
    tables = _tables(payload, price_field, code_field)
    return tables[0] if tables else None


def _bars_from_table(table: dict | None, day: date, market: str) -> list[Bar]:
    if not table:
        return []
    fields = table["fields"]
    code_index = fields.index("證券代號" if "證券代號" in fields else "代號")
    name_index = fields.index("證券名稱" if "證券名稱" in fields else "名稱")
    close_index = fields.index("收盤價" if "收盤價" in fields else "收盤")
    volume_index = fields.index("成交股數")
    bars: list[Bar] = []
    for row in table.get("data") or []:
        if len(row) <= max(code_index, name_index, close_index, volume_index):
            continue
        stock_id = str(row[code_index]).strip()
        if not is_common_stock(stock_id):
            continue
        close = _to_float(row[close_index])
        volume = _to_float(row[volume_index])
        if close is None or close <= 0 or volume is None or volume < 0:
            continue
        bars.append(
            Bar(
                date=day,
                stock_id=stock_id,
                name=" ".join(str(row[name_index]).split()),
                market=market,
                close=close,
                volume=volume,
            )
        )
    return bars


def _foreign_column(fields: list[str], rows: list, code_name: str, net_name: str) -> dict[str, float]:
    if code_name not in fields or net_name not in fields:
        if not rows:
            return {}
        raise RuntimeError(f"三大法人缺少欄位 {net_name}")
    return _foreign_by_index(fields, rows, fields.index(code_name), fields.index(net_name))


def _foreign_by_index(fields: list[str], rows: list, code_index: int, net_index: int) -> dict[str, float]:
    del fields
    result: dict[str, float] = {}
    for row in rows:
        if len(row) <= max(code_index, net_index):
            continue
        stock_id = str(row[code_index]).strip()
        if not is_common_stock(stock_id):
            continue
        net = _to_float(row[net_index])
        if net is None:
            continue
        result[stock_id] = net
    return result


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("，", "")
    if text in {"", "--", "---", "X", "nan", "None", "null", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None
