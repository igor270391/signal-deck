#!/usr/bin/env python3
"""
Тягне новини з публічних RSS (CoinDesk, Cointelegraph, Decrypt), додає до
кожної новини простий евристичний аналіз впливу (ключові слова -- НЕ LLM,
НЕ професійна аналітика), групує по даті публікації для календаря і зберігає:

  data/news/history/<YYYY-MM-DD>.json   -- усі новини за конкретний день
  data/news/index.json                   -- {дата: кількість новин} для календаря
  data/news/latest.json                  -- вказівник на останню дату з даними

Дедуплікація між запусками -- по посиланню (link), тож той самий запуск
щогодини не плодить дублікати однієї статті.
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
NEWS_HISTORY_DIR = NEWS_DIR / "history"

FEEDS = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed"},
]

MAX_ITEMS_PER_FEED = 15
SNIPPET_LEN = 200

# -- монети, які розпізнаються в тексті новини --------------------------
# full_names шукаються в тексті у нижньому регістрі (без ризику плутанини).
# Для тікерів з високим ризиком колізії зі звичайними словами (LINK, DOT,
# NEAR, SOL, ADA, BNB) повна назва -- основний спосіб розпізнавання; сам
# тікер перевіряється лише як ОКРЕМЕ слово ВЕЛИКИМИ ЛІТЕРАМИ в оригінальному
# (не lowercased) тексті, як зазвичай пишуть у заголовках ($BTC, BTC).
COIN_FULLNAMES = {
    "BTC": ["bitcoin"],
    "ETH": ["ethereum", "ether"],
    "SOL": ["solana"],
    "XRP": ["ripple", "xrp"],
    "BNB": ["binance coin", "binancecoin"],
    "ADA": ["cardano"],
    "DOGE": ["dogecoin"],
    "AVAX": ["avalanche"],
    "LINK": ["chainlink"],
    "DOT": ["polkadot"],
    "NEAR": ["near protocol"],
    "ONDO": ["ondo finance", "ondo"],
}

POSITIVE_WORDS = [
    "rally", "rallies", "surge", "surges", "soar", "soars", "adoption",
    "approval", "approves", "approved", "partnership", "upgrade", "bullish",
    "inflow", "inflows", "institutional", "record high", "all-time high",
    "integrates", "integration", "launch", "launches", "expands",
    "expansion", "rebound", "recovery", "breakthrough", "milestone",
]

NEGATIVE_WORDS = [
    "hack", "hacked", "exploit", "exploited", "breach", "ban", "banned",
    "lawsuit", "sues", "sued", "charges", "charged", "crash", "crashes",
    "dump", "dumps", "bearish", "crackdown", "delist", "delisting",
    "fraud", "scam", "outage", "collapse", "bankrupt", "bankruptcy",
    "liquidation", "liquidated", "sell-off", "selloff", "plunge",
    "plunges", "fine", "fined", "penalty", "stolen", "theft",
]


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


def find_coins(original_text: str, lower_text: str):
    matches = []
    for label, full_names in COIN_FULLNAMES.items():
        found = any(name in lower_text for name in full_names)
        if not found:
            # окреме слово ВЕЛИКИМИ ЛІТЕРАМИ (тікер) або $TICKER в оригінальному тексті
            found = bool(re.search(rf"(?<![A-Za-z]){label}(?![a-z])", original_text))
        if found:
            matches.append(label)
    return matches


def analyze_impact(title: str, snippet: str):
    original = f"{title} {snippet}"
    lower = original.lower()

    coins = find_coins(original, lower)
    pos_hits = [w for w in POSITIVE_WORDS if w in lower]
    neg_hits = [w for w in NEGATIVE_WORDS if w in lower]

    if len(neg_hits) > len(pos_hits):
        impact = "негативний"
    elif len(pos_hits) > len(neg_hits):
        impact = "позитивний"
    else:
        impact = "нейтральний"

    coins_str = ", ".join(coins) if coins else "загального ринку"
    if impact == "позитивний":
        note = f"Позитивний тон для {coins_str} за ключовими словами."
    elif impact == "негативний":
        note = f"Негативний тон для {coins_str} за ключовими словами -- варто звернути увагу."
    else:
        note = f"Нейтрально щодо {coins_str} -- прямого сигналу на ціну за ключовими словами не видно."

    triggers = (pos_hits[:3] if impact == "позитивний" else neg_hits[:3] if impact == "негативний" else [])

    return {
        "impact": impact,
        "coins": coins,
        "note": note,
        "triggers": triggers,
    }


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

        analysis = analyze_impact(title, desc)

        items.append({
            "source": source_name,
            "title": title,
            "link": link,
            "snippet": desc,
            "published_at": published_at,
            **analysis,
        })
    return items


def main():
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    all_items = []
    errors = []
    for feed in FEEDS:
        try:
            root = fetch_feed(feed["url"])
            all_items.extend(parse_items(root, feed["name"]))
        except Exception as e:  # noqa: BLE001
            errors.append({"source": feed["name"], "error": str(e)})
            print(f"[WARN] {feed['name']}: {e}", file=sys.stderr)

    if not all_items:
        print("Жодної новини не вдалось отримати — файли не оновлюємо.", file=sys.stderr)
        sys.exit(1)

    # групуємо по даті публікації (якщо дати немає -- відносимо на сьогодні)
    by_date = {}
    for item in all_items:
        d = item["published_at"][:10] if item["published_at"] else today_str
        by_date.setdefault(d, []).append(item)

    NEWS_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    index_path = NEWS_DIR / "index.json"
    index = {}
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)

    for date_str, items in by_date.items():
        day_file = NEWS_HISTORY_DIR / f"{date_str}.json"
        existing_items = []
        if day_file.exists():
            with open(day_file, "r", encoding="utf-8") as f:
                existing_items = json.load(f).get("items", [])

        merged = {it["link"]: it for it in existing_items}
        for it in items:
            merged[it["link"]] = it  # свіжі дані перекривають старі (той самий лінк)
        merged_list = sorted(merged.values(), key=lambda x: x["published_at"] or "", reverse=True)

        with open(day_file, "w", encoding="utf-8") as f:
            json.dump({
                "date": date_str,
                "generated_at": now.isoformat(),
                "count": len(merged_list),
                "items": merged_list,
            }, f, ensure_ascii=False, indent=2)

        index[date_str] = len(merged_list)

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    latest_date = max(index.keys())
    with open(NEWS_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump({"date": latest_date, "generated_at": now.isoformat()}, f, ensure_ascii=False, indent=2)

    print(f"OK: оброблено {len(all_items)} новин за {len(by_date)} днів ({len(errors)} помилок джерел)")


if __name__ == "__main__":
    main()
