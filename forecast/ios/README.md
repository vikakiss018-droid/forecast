# Forecast iOS (нативный, без Expo)

SwiftUI-приложение: те же сетапы, симуляция и пуши, данные только с вашего Forecast-сервера.

App Store и Expo не нужны. Ставите с Mac через Xcode на свой iPhone.

## Установка на телефон

1. Откройте `forecast/ios/Forecast.xcodeproj` в Xcode.
2. Выберите target **Forecast** → Signing & Capabilities:
   - Team — ваш Apple Developer
   - Bundle ID `com.forecast.scanner` (или свой, тогда тот же ID пропишите в `.env` как `APNS_BUNDLE_ID`)
3. Подключите iPhone, выберите его как destination, нажмите Run.
4. На iPhone: Настройки → Основные → VPN и управление устройством → доверить сертификату.

В приложении: шестерёнка → адрес сервера `http://IP:8000`, логин/пароль панели `/scanner`, включить уведомления.

## Пуши (когда приложение закрыто)

На developer.apple.com:

1. Certificates, Identifiers & Profiles → Keys → создать ключ **Apple Push Notifications service (APNs)**.
2. Скачать `.p8` один раз, запомнить Key ID.
3. Team ID — в правом верхнем углу аккаунта.
4. Для App ID `com.forecast.scanner` включить Push Notifications.

На сервере в `.env`:

```bash
APNS_KEY_PATH=/opt/forecast/secrets/AuthKey_XXXXXXXXXX.p8
APNS_KEY_ID=XXXXXXXXXX
APNS_TEAM_ID=XXXXXXXXXX
APNS_BUNDLE_ID=com.forecast.scanner
# true пока ставите из Xcode (Debug). false — для Release/ad-hoc.
APNS_USE_SANDBOX=true
```

Положить `.p8` вне git. После этого `git pull` и `sudo systemctl restart forecast-api`.

Пока ключа нет, приложение всё равно показывает сетапы; пуш с сервера просто не уйдёт.
