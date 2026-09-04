# Forecast Mobile (iOS / Android)

Отдельное нативное приложение — не вкладка браузера. Подключается к вашему Forecast API и показывает выгодные позиции (score > 35), с push-уведомлениями после скана.

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

## Сборка APK / IPA (полноценное приложение)

```bash
cd forecast/mobile
npm install -g eas-cli
eas login
eas init          # создаст projectId в app.json
eas build -p android --profile preview   # APK для Android
eas build -p ios --profile preview     # нужен Apple Developer
```

После сборки скачайте файл и установите на телефон (Android — APK напрямую, iOS — через TestFlight или ad-hoc).

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

- Список выгодных сетапов (фильтр «Выгодные» / «Все»)
- Автообновление каждые 20 секунд
- Локальное уведомление при новом скане (если приложение открыто)
- Push с сервера после hourly-скана (если приложение закрыто)
- Отдельная иконка на экране телефона после установки APK/IPA
