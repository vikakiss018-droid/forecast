import SwiftUI

struct PaperView: View {
    let onOpenSettings: () -> Void
    @EnvironmentObject private var store: SettingsStore
    @State private var data: PaperResponse?
    @State private var loading = false
    @State private var error: String?
    @State private var updating = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Симуляция").font(.system(size: 24, weight: .heavy)).foregroundStyle(Theme.text)
                    Text("порог \(Theme.num(data?.summary.minScore, digits: 0)) · \(Theme.time(data?.summary.updatedAt))")
                        .font(.system(size: 13)).foregroundStyle(Theme.muted)
                }
                Spacer()
                HStack(spacing: 8) {
                    Button {
                        Task { await runPaperUpdate() }
                    } label: {
                        Text(updating ? "Обновление…" : "Обновить цены")
                            .font(.system(size: 13, weight: .heavy))
                            .foregroundStyle(Color(red: 42 / 255, green: 16 / 255, blue: 32 / 255))
                            .padding(.horizontal, 12)
                            .padding(.vertical, 9)
                            .background(Theme.accent.opacity(store.settings.password.isEmpty || updating ? 0.45 : 1), in: Capsule())
                    }
                    .disabled(store.settings.password.isEmpty || updating)
                    settingsButton(onOpenSettings)
                }
            }
            .padding(.top, 8)

            HStack(spacing: 8) {
                stat("Открыто", data.map { String($0.summary.open ?? 0) } ?? "—")
                stat("Win%", data?.summary.winRatePct.map { "\(Theme.num($0, digits: 1))%" } ?? "—")
                stat("Total R", totalRText)
            }

            ScrollView {
                LazyVStack(spacing: 12) {
                    if store.settings.password.isEmpty {
                        OfflineBanner(
                            text: "Симуляция подтянется с сервера. Сейчас виден только каркас экрана.",
                            actionTitle: "Открыть настройки",
                            action: onOpenSettings
                        )
                    } else if let error {
                        OfflineBanner(text: error, actionTitle: "Настройки", action: onOpenSettings)
                    }
                    if (data?.trades ?? []).isEmpty {
                        EmptyFeedCard(
                            title: loading ? "Загрузка…" : "Нет бумажных сделок",
                            subtitle: "Сделки появятся, когда скан на сервере найдёт сетап выше порога."
                        )
                        SkeletonSetupCard()
                    } else {
                        ForEach(data?.trades ?? []) { TradeCardView(trade: $0) }
                    }
                }
                .padding(.bottom, 24)
            }
            .refreshable { await load(silent: true) }
        }
        .padding(.horizontal, 14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .task(id: store.settings.baseURL + store.settings.password) {
            await load(silent: false)
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 20_000_000_000)
                if !store.settings.password.isEmpty {
                    await load(silent: true)
                }
            }
        }
    }

    private var totalRText: String {
        guard let r = data?.summary.totalR else { return "—" }
        let sign = r > 0 ? "+" : ""
        return "\(sign)\(Theme.num(r, digits: 2))"
    }

    private func load(silent: Bool) async {
        if store.settings.password.isEmpty {
            loading = false
            return
        }
        if !silent { loading = true }
        do {
            data = try await store.settings.client.fetchPaper()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        loading = false
    }

    private func runPaperUpdate() async {
        updating = true
        error = nil
        do {
            _ = try await store.settings.client.startScan(kind: .paper)
            await load(silent: true)
        } catch {
            self.error = error.localizedDescription
        }
        updating = false
    }
}
