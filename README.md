# Signal Deck

Статичний дашборд крипто-сигналів: тягне денні свічки з публічного Bybit API,
рахує RSI14 / EMA20 / EMA50 / ATR14, класифікує кожен актив як ЛОНГ / НЕЙТРАЛ / ШОРТ,
і зберігає знімок у JSON, який показує календар/таймлайн на фронтенді.

**Це не фінансова порада.** Все — автоматичний технічний розрахунок, без гарантій.

## Як це працює

```
scripts/fetch_signals.py   →  тягне дані з Bybit, рахує індикатори
        │
        ▼
data/history/<дата>/<час>.json   →  архів знімків (для календаря)
data/latest.json                →  останній знімок
data/index.json                 →  список дат/часів (щоб фронтенд знав, що показувати)
        │
        ▼
index.html   →  читає data/*.json і рендерить картки, без бекенду
```

`.github/workflows/update.yml` запускає скрипт щогодини (cron `5 * * * *`) і
автоматично комітить оновлені JSON-файли назад у репозиторій.

## Як розгорнути на GitHub Pages

1. Створи новий репозиторій на GitHub і заливай туди весь цей проєкт:
   ```bash
   git init
   git add .
   git commit -m "init signal deck"
   git branch -M main
   git remote add origin https://github.com/<твій-юзернейм>/<репо>.git
   git push -u origin main
   ```

2. У налаштуваннях репозиторію: **Settings → Actions → General → Workflow permissions**
   постав **"Read and write permissions"** — інакше workflow не зможе закомітити оновлені дані.

3. Запусти перший знімок вручну, щоб не чекати годину:
   **Actions → Update crypto signals → Run workflow**.

4. Увімкни GitHub Pages: **Settings → Pages → Source: Deploy from a branch → main / (root)**.

5. Через кілька хвилин сайт буде доступний за адресою
   `https://<твій-юзернейм>.github.io/<репо>/`

## Локальний запуск скрипта (для тесту)

```bash
python3 scripts/fetch_signals.py
```

Потребує лише стандартної бібліотеки Python (`urllib`, `json`) — нічого додатково ставити не треба.

## Що можна додати далі

- **Новини** (CoinDesk/CryptoTimes RSS) — окремий скрипт `fetch_news.py`, який кладе
  результат у `data/news/<дата>.json`. RSS зазвичай не віддає CORS-заголовки, тому
  парсити його треба саме тут, на бекенді/Actions, а не з браузера.
- **LLM-висновки** — після збору цифр викликати Anthropic/OpenAI API з промптом
  "ось RSI/EMA/зміна ціни, напиши 2–3 речення висновку" і зберігати текст поруч
  із цифрами в тому ж JSON.
- **Графіки/Стакан/Прогнози** — окремі JSON-файли за тим самим принципом
  (наприклад `data/orderbook/<дата>/<час>.json` з Bybit orderbook endpoint).
