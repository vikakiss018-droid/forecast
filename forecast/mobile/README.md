# Forecast Mobile

Нативная iOS-версия (без Expo) лежит в [`../ios`](../ios). Её и ставьте на телефон через Xcode.

Эта папка `mobile/` — старый прототип на Expo, больше не используется.


## Быстрый старт (разработка)

```bash
cd forecast/mobile
npm install
npx expo start
```

1. Установите **Expo Go** на телефон (App Store / Google Play).
2. Отсканируйте QR-код из терминала.
3. В приложении откройте **Настройки** (⚙):
   - **Адрес сервера:** `http://IP-ВАШЕГО-VPS:8000`
   - **Логин / пароль:** как у панели `/scanner` (`PANEL_AUTH_USER` / `PANEL_AUTH_PASSWORD`)
4. Включите **Уведомления score > 35** и нажмите **Сохранить**.

> Expo Go — для проверки. Для «настоящего» приложения на иконке телефона без Expo Go соберите APK/IPA ниже.

## EAS (своё приложение на телефоне, без App Store)

Нужен бесплатный аккаунт на [expo.dev](https://expo.dev) (это не Apple Developer). Apple ID разработчика понадобится только на шаге `eas build -p ios`.

```bash
cd forecast/mobile
npm install
npx eas login
npx eas init
npx eas build -p ios --profile preview
```

`eas init` запишет настоящий `projectId` в `app.json` — без него серверные push не работают.

Профиль `preview` — **internal / ad hoc**: ставится только на зарегистрированные iPhone, в App Store не публикуется. После сборки откройте ссылку из терминала на телефоне и установите.

На iPhone один раз: Настройки → Основные → VPN и управление устройством → доверять сертификату.

### Android APK локально (без EAS)

```bash
npx expo prebuild
cd android && ./gradlew assembleRelease
```

APK: `android/app/build/outputs/apk/release/app-release.apk`

## Сервер

На VPS должны быть:

1. Обновлённый код с `/m/api/setups` и `/m/api/expo/register`
2. Перезапуск API:

```bash
cd /opt/forecast
sudo -u forecast git pull
sudo -u forecast .venv/bin/pip install -r requirements.txt
sudo systemctl restart forecast-api
```

3. В `.env`:

```bash
MOBILE_ALERT_MIN_SCORE=35
PANEL_AUTH_USER=admin
PANEL_AUTH_PASSWORD=ваш_пароль
```

Push с сервера работает через **Expo Push** — токен регистрируется при включении уведомлений в приложении.

## Что умеет приложение

Телефон только показывает данные с сервера. Сканы, score, симуляция и пуши считаются на VPS.

- Сканер / среднесрок / акции — готовые сетапы (фильтр «Выгодные» / «Все»)
- Симуляция — открытые и закрытые бумажные сделки
- Автообновление каждые 20 секунд
- Локальное уведомление при новом скане (если приложение открыто)
- Push с сервера после hourly-скана (если приложение закрыто)
- Отдельная иконка на экране телефона после установки APK/IPA
