#!/usr/bin/env python3
"""
Тягне заголовки новин з публічних RSS-фідів (без ключів і реєстрації),
парсить title/link/дату/короткий опис і зберігає в data/news/latest.json.

Джерела: CoinDesk, Cointelegraph, Decrypt -- усі мають стабільні публічні RSS.
(В оригінальному дашборді фігурували CryptoFeed/CoinDesk/CryptoTimes, але
CryptoFeed схожий на власний контент того блогера, а не публічний RSS, тож
тут використано три перевірені джерела з реальними RSS-адресами.)
"""

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib import request, error

ROOT = Path(__file__).resolve().parent.parent
NEWS_DIR = ROOT / "data" / "news"

FEEDS = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed"},
]

MAX_ITEMS_PER_FEED = 15
MAX_TOTAL_ITEMS = 40
SNIPPET_LEN = 200


def strip_html(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_feed(url: str, retries: int = 3):
    last_err = None
    for attempt in range(retries):
        try:
            req = request.Request(url, headers={"User-Agent": "signal-deck-news/1.0"})
            with request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            return ET.fromstring(data)
        except (error.URLError, error.HTTPError, ET.ParseError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"не вдалось отримати {url}: {last_err}")


def parse_items(root, source_name):
    items = []
    channel_items = root.findall("./channel/item")
    if not channel_items:
        channel_items = root.findall(".//item")

    for item in channel_items[:MAX_ITEMS_PER_FEED]:
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")
        date_el = item.find("pubDate")

        title = strip_html(title_el.text) if title_el is not None and title_el.text else None
        link = (link_el.text or "").strip() if link_el is not None and link_el.text else None
        desc = strip_html(desc_el.text) if desc_el is not None and desc_el.text else ""
        if len(desc) > SNIPPET_LEN:
            desc = desc[:SNIPPET_LEN].rsplit(" ", 1)[0] + "…"

        published_at = None
        if date_el is not None and date_el.text:
            try:
                published_at = parsedate_to_datetime(date_el.text).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                published_at = None

        if not title or not link:
            continue

        items.append({
            "source": source_name,
            "title": title,
            "link": link,
            "snippet": desc,
            "published_at": published_at,
        })
    return items


def main():
    all_items = []
    errors = []

    for feed in FEEDS:
        try:
            root = fetch_feed(feed["url"])
            items = parse_items(root, feed["name"])
            all_items.extend(items)
        except Exception as e:  # noqa: BLE001
            errors.append({"source": feed["name"], "error": str(e)})
            print(f"[WARN] {feed['name']}: {e}", file=sys.stderr)

    if not all_items:
        print("Жодної новини не вдалось отримати — файл не оновлюємо.", file=sys.stderr)
        sys.exit(1)

    all_items.sort(key=lambda x: x["published_at"] or "", reverse=True)
    all_items = all_items[:MAX_TOTAL_ITEMS]

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(all_items),
        "items": all_items,
        "errors": errors,
    }

    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    with open(NEWS_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"OK: збережено {len(all_items)} новин ({len(errors)} помилок джерел)")


if __name__ == "__main__":
    main()
