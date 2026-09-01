/* Forecast mobile PWA — notifications when score > threshold */
const CACHE = "forecast-m-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("push", (event) => {
  let data = { title: "Выгодная позиция", body: "Score выше порога", url: "/scanner?mobile=1" };
  try {
    if (event.data) data = { ...data, ...event.data.json() };
  } catch (e) {
    try {
      data.body = event.data.text();
    } catch (e2) {}
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Forecast", {
      body: data.body || "",
      icon: "/m/icon-192.png",
      badge: "/m/icon-192.png",
      tag: data.tag || "forecast-hot",
      renotify: true,
      data: { url: data.url || "/m" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/scanner?mobile=1";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          client.focus();
          if ("navigate" in client) client.navigate(url);
          return;
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
