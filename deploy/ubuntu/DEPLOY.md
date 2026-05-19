# Развёртывание на Ubuntu (VPS 2 CPU / 2 GB)

Сканер работает **в фоне каждые 15 минут** и пишет кэш в `data/processed/market_scan_latest.json`.  
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
- `forecast-scan.timer` — скан **каждые 15 минут**

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
export FORECAST_MAX_SYMBOLS=100 FORECAST_TOP=10
python -m forecast.run_scheduled_scan
```

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
