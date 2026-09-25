import SwiftUI
import UserNotifications

struct FeedView: View {
    let kind: FeedKind
    let onOpenSettings: () -> Void
    @EnvironmentObject private var store: SettingsStore

    @State private var data: SetupsResponse?
    @State private var filterHot = true
    @State private var loading = false
    @State private var error: String?
    @State private var lastUpdated: String?
    @State private var lastKey = ""
    @State private var scanning = false
    @State private var scanLabel = ""
    @State private var scanCurrent = 0.0
    @State private var scanTotal = 0.0

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            stats
            filters
            if scanning || !scanLabel.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    if scanning {
                        ProgressView(value: scanFraction)
                            .tint(Theme.accent)
                    }
                    Text(scanLabel.isEmpty ? "Скан выполняется на сервере…" : scanLabel)
                        .font(.system(size: 12))
                        .foregroundStyle(Theme.muted)
                }
            }
            ScrollView {
                LazyVStack(spacing: 12) {
                    if needsSetup {
                        OfflineBanner(
                            text: "Сервер ещё не указан. Интерфейс уже здесь — живые сетапы появятся после адреса и пароля панели.",
                            actionTitle: "Открыть настройки",
                            action: onOpenSettings
                        )
                    } else if let error {
                        OfflineBanner(text: error, actionTitle: "Настройки", action: onOpenSettings)
                    }
                    if visible.isEmpty {
                        EmptyFeedCard(
                            title: loading ? "Загрузка…" : "Пока нет сетапов",
                            subtitle: filterHot
                                ? "Карточки с score выше \(Theme.num(threshold, digits: 0)) появятся, когда сервер пришлёт скан."
                                : "В последнем скане нет сетапов."
                        )
                        SkeletonSetupCard()
                        SkeletonSetupCard()
                    } else {
                        ForEach(visible) { SetupCardView(setup: $0) }
                    }
                }
                .padding(.bottom, 24)
            }
            .refreshable { await load(silent: true) }
        }
        .padding(.horizontal, 14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .task(id: kind.rawValue + store.settings.baseURL + store.settings.password) {
            data = nil
            filterHot = true
            lastUpdated = nil
            lastKey = ""
            await load(silent: false)
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 20_000_000_000)
                if !needsSetup {
                    await load(silent: true)
                }
            }
        }
    }

    private var needsSetup: Bool {
        store.settings.password.isEmpty || store.settings.baseURL.isEmpty
    }

    private var threshold: Double { data?.alertMinScore ?? 35 }
    private var visible: [Setup] {
        let all = data?.setups ?? []
        return filterHot ? all.filter(\.hot) : all
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(kind.title).font(.system(size: 24, weight: .heavy)).foregroundStyle(Theme.text)
                    if loading {
                        ProgressView().tint(Theme.accent).scaleEffect(0.8)
                    }
                }
                Text("\(data?.timeframe ?? kind.subtitle) · порог \(Theme.num(threshold, digits: 0))")
                    .font(.system(size: 13))
                    .foregroundStyle(Theme.muted)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 8) {
                scanButton
                settingsButton(onOpenSettings)
            }
        }
        .padding(.top, 8)
    }

    private var scanButton: some View {
        Button {
            Task { await startScan() }
        } label: {
            HStack(spacing: 6) {
                if scanning {
                    ProgressView().tint(Color(red: 42 / 255, green: 16 / 255, blue: 32 / 255)).scaleEffect(0.7)
                }
                Text(scanning ? "Сканер работает" : kind.runLabel)
                    .font(.system(size: 13, weight: .heavy))
            }
            .foregroundStyle(Color(red: 42 / 255, green: 16 / 255, blue: 32 / 255))
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(Theme.accent.opacity(scanning || needsSetup ? 0.45 : 1), in: Capsule())
        }
        .disabled(scanning || needsSetup)
    }

    private var scanFraction: Double {
        let tot = scanTotal
        guard tot > 0 else { return 0 }
        return min(1, scanCurrent / tot)
    }

    private var stats: some View {
        HStack(spacing: 8) {
            stat("Score > порога", data.map { String($0.hotCount ?? $0.setups.filter(\.hot).count) } ?? "—")
            stat("Сетапов", data.map { String($0.setups.count) } ?? "—")
            stat("Обновлено", Theme.time(data?.updatedAt), small: true)
        }
    }

    private var filters: some View {
        HStack(spacing: 8) {
            chip("Выгодные", filterHot) { filterHot = true }
            chip("Все", !filterHot) { filterHot = false }
        }
    }

    private func load(silent: Bool) async {
        if needsSetup {
            loading = false
            return
        }
        if !silent { loading = true }
        do {
            let next = try await store.settings.client.fetchFeed(kind)
            data = next
            error = nil
            if kind == .scan {
                let hot = next.setups.filter(\.hot)
                let key = "\(next.updatedAt ?? "")|\(hot.map { "\($0.symbol ?? ""):\($0.score)" }.joined(separator: ","))"
                if store.settings.notifyEnabled,
                   let prev = lastUpdated, let upd = next.updatedAt, prev != upd,
                   !hot.isEmpty, key != lastKey {
                    let title = hot.count == 1 ? "Выгодная позиция" : "\(hot.count) выгодные позиции"
                    let body = hot.prefix(3).map { "\($0.symbol ?? "") \($0.direction ?? "") · \(Theme.num($0.score, digits: 1))" }.joined(separator: " · ")
                    await localNotify(title: title, body: body)
                }
                lastKey = key
                lastUpdated = next.updatedAt
            }
        } catch {
            self.error = error.localizedDescription
        }
        loading = false
    }

    private func startScan() async {
        if needsSetup { return }
        scanning = true
        scanLabel = "Запуск скана на сервере…"
        error = nil
        do {
            let result = try await store.settings.client.startScan(kind: kind)
            if result.busy == true {
                scanLabel = "Скан уже выполняется на сервере"
            }
            await pollScan()
        } catch APIError.status(404) {
            scanning = false
            scanLabel = ""
            error = "На сервере нет API скана. Обновите forecast-api (git pull + restart)."
        } catch {
            scanning = false
            scanLabel = ""
            self.error = error.localizedDescription
        }
    }

    private func pollScan() async {
        for _ in 0..<900 {
            do {
                let p = try await store.settings.client.fetchProgress(kind: kind)
                scanLabel = p.label
                scanCurrent = p.progress?.current ?? 0
                scanTotal = p.progress?.total ?? 0
                if p.isRunning {
                    scanning = true
                } else {
                    if (p.status ?? "") == "error" {
                        self.error = p.error ?? "Скан завершился с ошибкой"
                    }
                    await load(silent: true)
                    scanning = false
                    if scanLabel.isEmpty { scanLabel = "Скан завершён" }
                    return
                }
            } catch {
                break
            }
            try? await Task.sleep(nanoseconds: 2_000_000_000)
        }
        scanning = false
    }

    private func localNotify(title: String, body: String) async {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        let req = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        try? await UNUserNotificationCenter.current().add(req)
    }
}

func settingsButton(_ action: @escaping () -> Void) -> some View {
    Button(action: action) {
        Text("⚙")
            .font(.system(size: 20))
            .frame(width: 42, height: 42)
            .background(Theme.text.opacity(0.06), in: Circle())
            .overlay(Circle().stroke(Theme.border))
    }
}

func stat(_ label: String, _ value: String, small: Bool = false) -> some View {
    VStack(alignment: .leading, spacing: 4) {
        Text(label.uppercased()).font(.system(size: 10)).foregroundStyle(Theme.muted)
        Text(value)
            .font(.system(size: small ? 12 : 16, weight: .bold))
            .foregroundStyle(Theme.text)
            .lineLimit(1)
            .minimumScaleFactor(0.7)
    }
    .padding(10)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(Theme.text.opacity(0.06), in: RoundedRectangle(cornerRadius: 16))
    .overlay(RoundedRectangle(cornerRadius: 16).stroke(Theme.border))
}

func chip(_ label: String, _ on: Bool, action: @escaping () -> Void) -> some View {
    Button(action: action) {
        Text(label)
            .font(.system(size: 13, weight: .bold))
            .foregroundStyle(on ? Color(red: 42 / 255, green: 16 / 255, blue: 32 / 255) : Theme.text)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(on ? Theme.accent : Theme.text.opacity(0.06), in: Capsule())
            .overlay(Capsule().stroke(on ? Theme.accent : Theme.border))
    }
}
