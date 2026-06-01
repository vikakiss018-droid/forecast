# Развёртывание на Ubuntu (VPS 2 CPU / 2 GB)

Сканер работает **в фоне каждый час в :03 UTC** (после закрытия 1h-свечи) и пишет кэш в `data/processed/market_scan_latest.json`.  
Веб-панель **читает кэш** (быстро), не гоняет полный скан на каждый запрос.

## 1. Подготовка сервера

```bash
sudo apt update && sudo apt install -y git python3 python3-venv
```

## 2. GitHub (один раз с Mac)

```bash
cd "/Users/st_sav/Desktop/Новая папка 2/Forecast_app"
git init
git branch -M main
git add .
git status   # убедитесь: нет .env и папки .venv
git commit -m "Initial commit: forecast scanner and deploy"
```

На [github.com/new](https://github.com/new) создайте репозиторий (например `forecast`), **без** README/license — пустой.

```bash
git remote add origin https://github.com/ВАШ_ЛОГИН/forecast.git
git push -u origin main
```

> Файл `.env` с ключами Binance **не попадает в git** (см. `.gitignore`). На сервере создайте его вручную из `.env.example`.

## 3. Клонирование на сервер

```bash
sudo mkdir -p /opt
sudo git clone https://github.com/ВАШ_ЛОГИН/forecast.git /opt/forecast
cd /opt/forecast
sudo cp .env.example .env
sudo nano .env   # ключи Binance (публичные данные работают и без ключей)
```

## 4. Установка (systemd)

```bash
sudo bash deploy/ubuntu/install.sh
```

Скрипт создаёт пользователя `forecast`, venv, включает:
- `forecast-api` — uvicorn на порту **8000**
- `forecast-scan.timer` — тренд-скан **каждый час в :03 UTC**

Настройки скана: `/opt/forecast/deploy/ubuntu/forecast.env` (скопирован из `forecast.env.example`).

После правки env:

```bash
sudo systemctl restart forecast-scan.timer
sudo systemctl start forecast-scan.service   # разовый прогон сейчас
```

## 5. Проверка

```bash
# API
curl -s http://127.0.0.1:8000/scanner/json | head

# Логи
sudo journalctl -u forecast-api -f
sudo journalctl -u forecast-scan -f
sudo systemctl list-timers | grep forecast
```

Панель в браузере: `http://IP-СЕРВЕРА:8000/scanner`

### Пароль на панель

В `/opt/forecast/.env`:

```bash
PANEL_AUTH_USER=admin
PANEL_AUTH_PASSWORD=ваш_длинный_пароль
chown forecast:forecast /opt/forecast/.env
sudo systemctl restart forecast-api
```

Браузер запросит логин/пароль при открытии `/scanner`. Без `PANEL_AUTH_PASSWORD` панель остаётся открытой (не рекомендуется).  
Принудительный живой скан (тяжёлый): `?live=1`

## 6. Файрвол (опционально)

```bash
sudo ufw allow 22/tcp
sudo ufw allow 8000/tcp
sudo ufw enable
```

## 7. Nginx + HTTPS (опционально)

Проксируйте `location /` на `http://127.0.0.1:8000`, закройте прямой доступ к 8000 снаружи.

## Ручной скан без systemd

```bash
cd /opt/forecast
source .venv/bin/activate
# Параметры — configs/config.yaml (секции reference_backtest, trend_scan, auto_trade)
# Сверьте .env с .env.example перед запуском
python -m forecast.run_scheduled_scan
```

Таймер systemd: **каждый час в :03 UTC** (`forecast-scan.timer`), не каждые 15 мин — как закрытие 1h-свечи в бэктесте.

Перед первым запуском сгенерируйте список 50 пар:

```bash
python -m forecast.run_symbol_ranking
```

## Автоторговля (опционально)

После тренд-скана открывается позиция на **Binance Spot** (**long only**), топ-N сетапов из 50 filtered пар.

1. В Binance API: **Enable Spot & Margin Trading** (без Withdraw).
2. USDT на спотовом кошельке.
3. В `/opt/forecast/.env` — **отдельный торговый ключ** (рекомендуется):

```bash
BINANCE_TRADE_API_KEY=ваш_новый_ключ
BINANCE_TRADE_API_SECRET=ваш_новый_секрет
```

Старый ключ можно оставить только для сканера (`BINANCE_API_KEY` / `SECRET`, read-only).

4. Автоторговля и остальное в `.env`:

```bash
AUTO_TRADE_ENABLED=true
AUTO_TRADE_DRY_RUN=true
AUTO_TRADE_MARKET=spot
AUTO_TRADE_MIN_SCORE=18
AUTO_TRADE_MIN_PROB_PCT=50
AUTO_TRADE_MAX_NOTIONAL_USDT=50
AUTO_TRADE_RISK_PCT=0.5
AUTO_TRADE_LEVERAGE=1
FORECAST_USE_FILTERED=1
FORECAST_LONG_ONLY=1
```

4. Dry-run: `sudo -u forecast .venv/bin/python -m forecast.run_auto_trade`
5. Статус: `http://IP:8000/trader/status`
6. Реальная торговля: `AUTO_TRADE_DRY_RUN=false` (малый notional сначала).

## Обновление кода (после push с Mac)

На Mac:

```bash
git add -A && git commit -m "описание изменений" && git push
```

На сервере:

```bash
cd /opt/forecast
sudo -u forecast git pull
sudo -u forecast .venv/bin/pip install -r requirements.txt
sudo systemctl restart forecast-api
# при изменении deploy/ubuntu/*:
sudo bash deploy/ubuntu/install.sh
```
