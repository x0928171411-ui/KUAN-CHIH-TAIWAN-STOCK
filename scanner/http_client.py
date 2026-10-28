"""帶重試與快取的 JSON 請求。交易日會重複查到，快取可避免同一天打兩次。"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class HttpClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            }
        )
        self._cache: dict[tuple[Any, ...], Any] = {}

    def get_json(
        self,
        url: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        pause: float = 0.0,
        retries: int = 3,
    ) -> Any:
        key = (url, tuple(sorted((params or {}).items())), tuple(sorted((headers or {}).items())))
        if key in self._cache:
            return self._cache[key]

        last_error: Exception | None = None
        for attempt in range(retries):
            if attempt == 0 and pause:
                time.sleep(pause)
            elif attempt:
                time.sleep(min(8, 2**attempt))
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=40)
                if response.status_code in {400, 401, 403}:
                    detail = " ".join(response.text.split())[:180]
                    raise RuntimeError(f"資料源拒絕請求：{detail}")
                if response.status_code in {429, 500, 502, 503, 504}:
                    last_error = RuntimeError(f"HTTP {response.status_code} {url}")
                    logger.warning("暫時失敗 %s %s，準備重試", response.status_code, url)
                    continue
                response.raise_for_status()
                text = response.text.lstrip("\ufeff \n\r\t")
                if not text or text[0] not in "{[":
                    last_error = ValueError(f"回傳不是 JSON：{url}")
                    logger.warning("回傳不是 JSON，準備重試 %s", url)
                    continue
                body = response.json()
                if isinstance(body, dict) and body.get("status") not in (None, 200):
                    message = str(body.get("msg") or body.get("status"))
                    raise RuntimeError(f"資料源拒絕請求：{message}")
                self._cache[key] = body
                return body
            except RuntimeError:
                raise
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                logger.warning("請求失敗（第 %s 次）%s：%s", attempt + 1, url, exc.__class__.__name__)
        raise RuntimeError(f"無法取得資料：{url}") from last_error
