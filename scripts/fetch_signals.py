#!/usr/bin/env python3
"""
Тягне денні ціни закриття з публічного CoinGecko API (без ключа), рахує
RSI14 / EMA20 / EMA50 та псевдо-ATR (на основі close-to-close руху, бо
безкоштовний market_chart-ендпоінт не дає high/low), рахує сигнал
(ЛОНГ / НЕЙТРАЛ / ШОРТ) по кожній монеті і зберігає результат у:

  data/history/<YYYY-MM-DD>/<HH-MM>.json   -- знімок на конкретний момент
  data/latest.json                          -- останній знімок (для фронтенду за замовч.)
  data/index.json                           -- список усіх доступних дат/часів (для календаря)

Запускається за розкладом через GitHub Actions (.github/workflows/update.yml).
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import request, error

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"

# id -- це офіційний CoinGecko coin id (не тикер), він потрібен для URL.
SYMBOLS = [
    {"id": "bitcoin", "label": "BTC"},
    {"id": "ethereum", "label": "ETH"},
    {"id": "solana", "label": "SOL"},
    {"id": "ripple", "label": "XRP"},
    {"id": "binancecoin", "label": "BNB"},
    {"id": "cardano", "label": "ADA"},
    {"id": "dogecoin", "label": "DOGE"},
    {"id": "avalanche-2", "label": "AVAX"},
    {"id": "chainlink", "label": "LINK"},
    {"id": "polkadot", "label": "DOT"},
    {"id": "near", "label": "NEAR"},
    {"id": "ondo-finance", "label": "ONDO"},
    {"id": "uniswap", "label": "UNI"},
    {"id": "aster-2", "label": "ASTER"},
    {"id": "cosmos", "label": "ATOM"},
    {"id": "aave", "label": "AAVE"},
    {"id": "aptos", "label": "APT"},
    {"id": "layerzero", "label": "ZRO"},
    {"id": "fartcoin", "label": "FARTCOIN"},
    {"id": "pippin", "label": "PIPPIN"},
    {"id": "pudgy-penguins", "label": "PENGU"},
    {"id": "maple-finance", "label": "SYRUP"},
    {"id": "moo-deng", "label": "MOODENG"},
    {"id": "pump-fun", "label": "PUMP"},
]

COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/coins/{id}/market_chart"
    "?vs_currency=usd&days=100&interval=daily"
)


def fetch_closes(coin_id: str, retries: int = 4):
    url = COINGECKO_URL.format(id=coin_id)
    last_err = None
    for attempt in range(retries):
        try:
            req = request.Request(url, headers={"User-Agent": "signal-deck/1.0"})
            with request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            prices = payload.get("prices")
            if not prices:
                raise RuntimeError("порожня відповідь (немає 'prices')")
            closes = [p[1] for p in prices if p[1] and p[1] > 0]
            return closes
        except error.HTTPError as e:
            last_err = e
            # 429 = забагато запитів -- чекаємо довше і пробуємо ще раз
            wait = 8 if e.code == 429 else 2 * (attempt + 1)
            time.sleep(wait)
        except (error.URLError, RuntimeError, KeyError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"не вдалось отримати {coin_id}: {last_err}")


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


def pseudo_atr(closes, period=14):
    """ATR-подібна волатильність на основі close-to-close руху (немає high/low
    у безкоштовному market_chart-ендпоінті)."""
    diffs = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    if len(diffs) < period:
        return None
    window = diffs[-period:]
    return sum(window) / len(window)


def efficiency_ratio(closes, period=14):
    """Kaufman Efficiency Ratio -- заміна ADX для close-only даних (без high/low).

    ER = |чистий рух ціни за period| / (сума абсолютних денних рухів за period)

    ER -> 1  : ціна йшла прямо в один бік -- сильний тренд.
    ER -> 0  : ціна тупцювала туди-сюди -- флет/рейндж.
    Використовується, щоб вирішити, як інтерпретувати RSI: momentum (тренд)
    чи contrarian / перекупленість-перепроданість (флет).
    """
    if len(closes) < period + 1:
        return 0.5  # недостатньо даних -- нейтральне значення
    window = closes[-(period + 1):]
    net_change = abs(window[-1] - window[0])
    volatility = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    if volatility == 0:
        return 0.5
    return clamp(net_change / volatility, 0.0, 1.0)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def compute_signal(closes):
    n = len(closes)

    rsi_arr = rsi(closes, 14)
    ema_fast = ema(closes, 20)
    ema_slow = ema(closes, 50)

    last_close = closes[-1]
    last_rsi = rsi_arr[-1] or 50
    trend_up = (ema_fast[-1] or last_close) > (ema_slow[-1] or last_close)

    idx30 = max(0, n - 30)
    idx7 = max(0, n - 7)
    change30 = (last_close - closes[idx30]) / closes[idx30] * 100
    change7 = (last_close - closes[idx7]) / closes[idx7] * 100

    window = closes[max(0, n - 20):]
    resistance = max(window)
    support = min(window)

    # ADX-подібний компонент (Kaufman Efficiency Ratio): наскільки ринок
    # трендовий (1.0) чи флетовий/рейнджовий (0.0). Гейтить, як інтерпретувати RSI:
    #   er > 0.5 -> momentum (сильний RSI підтверджує тренд, тягне score в той самий бік)
    #   er < 0.5 -> contrarian (сильний RSI = перекупленість/перепроданість, тягне score НАЗАД)
    #   er = 0.5 -> RSI-компонент гаситься до нуля (жоден підхід не надійний у цій зоні)
    # довше вікно (30 днів), ніж у RSI (14) -- навмисно, щоб ER показував
    # "загальну картину" тренду, не корелюючи 1-в-1 з коротким RSI-моментумом
    er = efficiency_ratio(closes, 30)
    rsi_base = clamp((last_rsi - 50) * 0.55, -20, 20)
    rsi_component = rsi_base * (2 * er - 1)

    if er >= 0.55:
        rsi_mode = "МОМЕНТУМ"
    elif er <= 0.45:
        rsi_mode = "КОНТР-ТРЕНД"
    else:
        rsi_mode = "ЗМІШАНИЙ"

    score = 50
    score += 18 if trend_up else -18
    score += rsi_component
    score += clamp(change30 * 0.35, -14, 14)
    score = int(clamp(round(score), 2, 98))

    label = "НЕЙТРАЛ"
    if score >= 60:
        label = "ЛОНГ"
    elif score <= 40:
        label = "ШОРТ"

    target = last_close + (resistance - last_close) * 0.6 if label == "ЛОНГ" else resistance
    stop = support * 0.985 if label == "ЛОНГ" else resistance * 1.015

    atr_val = pseudo_atr(closes, 14)

    return {
        "price": round(last_close, 8),
        "rsi14": round(last_rsi, 1),
        "trend_up": trend_up,
        "trend_strength": round(er, 2),
        "rsi_mode": rsi_mode,
        "change_7d": round(change7, 2),
        "change_30d": round(change30, 2),
        "resistance": round(resistance, 8),
        "support": round(support, 8),
        "target": round(target, 8),
        "stop": round(stop, 8),
        "atr14": round(atr_val, 8) if atr_val else None,
        "score": score,
        "label": label,
    }


def main():
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H-%M")

    results = []
    errors = []
    for i, s in enumerate(SYMBOLS):
        try:
            closes = fetch_closes(s["id"])
            if len(closes) < 55:
                raise RuntimeError(f"недостатньо даних ({len(closes)} точок)")
            sig = compute_signal(closes)
            results.append({"symbol": s["id"], "name": s["label"], **sig})
        except Exception as e:  # noqa: BLE001
            errors.append({"symbol": s["id"], "error": str(e)})
            print(f"[WARN] {s['label']}: {e}", file=sys.stderr)
        # невелика пауза між запитами, щоб не впертися в rate limit CoinGecko
        if i < len(SYMBOLS) - 1:
            time.sleep(2.0)

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
