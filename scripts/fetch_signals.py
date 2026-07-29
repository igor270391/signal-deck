#!/usr/bin/env python3
"""
Тягне денні свічки з публічного Bybit API, рахує RSI14 / EMA20 / EMA50 / ATR14,
рахує сигнал (ЛОНГ / НЕЙТРАЛ / ШОРТ) по кожній монеті і зберігає результат у:

  data/history/<YYYY-MM-DD>/<HH-MM>.json   -- знімок на конкретний момент
  data/latest.json                          -- останній знімок (для фронтенду за замовч.)
  data/index.json                           -- список усіх доступних дат/часів (для календаря)

Запускається за розкладом через GitHub Actions (.github/workflows/update.yml).
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import request, error

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"

SYMBOLS = [
    {"fsym": "BTC", "label": "BTC"},
    {"fsym": "ETH", "label": "ETH"},
    {"fsym": "SOL", "label": "SOL"},
    {"fsym": "XRP", "label": "XRP"},
    {"fsym": "BNB", "label": "BNB"},
    {"fsym": "ADA", "label": "ADA"},
    {"fsym": "DOGE", "label": "DOGE"},
    {"fsym": "AVAX", "label": "AVAX"},
    {"fsym": "LINK", "label": "LINK"},
    {"fsym": "DOT", "label": "DOT"},
    {"fsym": "NEAR", "label": "NEAR"},
    {"fsym": "ONDO", "label": "ONDO"},
]

# CryptoCompare: безкоштовне публічне API, без ключа, не блокує запити з
# дата-центрів/CI (на відміну від Bybit чи Binance, які часто дають 403/451
# саме на IP-адресах GitHub Actions, AWS, Azure тощо).
CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/v2/histoday?fsym={fsym}&tsym=USD&limit=150"


def fetch_klines(fsym: str, retries: int = 3):
    url = CRYPTOCOMPARE_URL.format(fsym=fsym)
    last_err = None
    for attempt in range(retries):
        try:
            req = request.Request(url, headers={"User-Agent": "signal-deck/1.0"})
            with request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("Response") != "Success":
                raise RuntimeError(payload.get("Message", "cryptocompare error"))
            rows = payload["Data"]["Data"]
            candles = [
                {
                    "time": int(r["time"]),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                }
                for r in rows
                if r["close"] > 0  # cryptocompare нулями заповнює дні до лістингу монети
            ]
            return candles
        except (error.URLError, error.HTTPError, RuntimeError, KeyError) as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"не вдалось отримати {fsym}: {last_err}")


def ema(values, period):
    k = 2 / (period + 1)
    out = [None] * len(values)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes, period=14):
    out = [None] * len(closes)
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain, loss = max(diff, 0), max(-diff, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    return out


def atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    out = [None] * (len(trs) + 1)
    prev = sum(trs[:period]) / period
    out[period] = prev
    for i in range(period, len(trs)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i + 1] = prev
    return out


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def compute_signal(candles):
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    n = len(closes)

    rsi_arr = rsi(closes, 14)
    ema_fast = ema(closes, 20)
    ema_slow = ema(closes, 50)
    atr_arr = atr(highs, lows, closes, 14)

    last_close = closes[-1]
    last_rsi = rsi_arr[-1] or 50
    trend_up = (ema_fast[-1] or last_close) > (ema_slow[-1] or last_close)

    idx30 = max(0, n - 30)
    idx7 = max(0, n - 7)
    change30 = (last_close - closes[idx30]) / closes[idx30] * 100
    change7 = (last_close - closes[idx7]) / closes[idx7] * 100

    window = candles[max(0, n - 20):]
    resistance = max(c["high"] for c in window)
    support = min(c["low"] for c in window)

    score = 50
    score += 18 if trend_up else -18
    score += clamp((last_rsi - 50) * 0.55, -20, 20)
    score += clamp(change30 * 0.35, -14, 14)
    score = int(clamp(round(score), 2, 98))

    label = "НЕЙТРАЛ"
    if score >= 60:
        label = "ЛОНГ"
    elif score <= 40:
        label = "ШОРТ"

    target = last_close + (resistance - last_close) * 0.6 if label == "ЛОНГ" else resistance
    stop = support * 0.985 if label == "ЛОНГ" else resistance * 1.015

    return {
        "price": round(last_close, 8),
        "rsi14": round(last_rsi, 1),
        "trend_up": trend_up,
        "change_7d": round(change7, 2),
        "change_30d": round(change30, 2),
        "resistance": round(resistance, 8),
        "support": round(support, 8),
        "target": round(target, 8),
        "stop": round(stop, 8),
        "atr14": round(atr_arr[-1], 8) if atr_arr[-1] else None,
        "score": score,
        "label": label,
    }


def main():
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H-%M")

    results = []
    errors = []
    for s in SYMBOLS:
        try:
            candles = fetch_klines(s["fsym"])
            if len(candles) < 55:
                raise RuntimeError("недостатньо свічок")
            sig = compute_signal(candles)
            results.append({"symbol": f"{s['fsym']}USD", "name": s["label"], **sig})
        except Exception as e:  # noqa: BLE001
            errors.append({"symbol": s["fsym"], "error": str(e)})
            print(f"[WARN] {s['fsym']}: {e}", file=sys.stderr)

    if not results:
        print("Жодного активу не вдалось обробити — знімок не зберігаємо.", file=sys.stderr)
        sys.exit(1)

    snapshot = {
        "generated_at": now.isoformat(),
        "date": date_str,
        "time": time_str,
        "count": len(results),
        "assets": sorted(results, key=lambda r: -r["score"]),
        "errors": errors,
    }

    day_dir = HISTORY_DIR / date_str
    day_dir.mkdir(parents=True, exist_ok=True)
    with open(day_dir / f"{time_str}.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    with open(DATA_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # update index of available date -> [times]
    index_path = DATA_DIR / "index.json"
    index = {}
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    index.setdefault(date_str, [])
    if time_str not in index[date_str]:
        index[date_str].append(time_str)
        index[date_str].sort()
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"OK: збережено знімок {date_str} {time_str} ({len(results)} активів, {len(errors)} помилок)")


if __name__ == "__main__":
    main()
