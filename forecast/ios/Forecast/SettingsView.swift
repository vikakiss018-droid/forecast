import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var store: SettingsStore
    @EnvironmentObject private var push: PushManager
    @Environment(\.dismiss) private var dismiss

    @State private var draft: AppSettings = .default
    @State private var message: String?
    @State private var busy = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text("Телефон только читает готовые сетапы с сервера. Логин и пароль — те же, что для панели /scanner.")
                        .foregroundStyle(Theme.muted)
                        .font(.system(size: 14))

                    #if targetEnvironment(simulator)
                    Text("Симулятор видит сеть Mac. VPS: http://IP:8000  ·  API на этом Mac: http://127.0.0.1:8000  (не localhost). Пуши APNs в симуляторе не приходят, сетапы — да.")
                        .font(.system(size: 13))
                        .foregroundStyle(Theme.accent2)
                    #endif

                    field("Адрес сервера") {
                        TextField("http://123.45.67.89:8000", text: $draft.baseURL)
                            .textInputAutocapitalization(.never)
                            .keyboardType(.URL)
                            .autocorrectionDisabled()
                    }
                    field("Логин") {
                        TextField("admin", text: $draft.username)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }
                    field("Пароль") {
                        SecureField("пароль панели", text: $draft.password)
                            .textInputAutocapitalization(.never)
                    }

                    HStack(alignment: .center, spacing: 12) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Уведомления score > 35")
                                .font(.system(size: 15, weight: .bold))
                                .foregroundStyle(Theme.text)
                            Text("Сервер шлёт APNs после каждого нового скана")
                                .font(.system(size: 12))
                                .foregroundStyle(Theme.muted)
                        }
                        Spacer()
                        Toggle("", isOn: $draft.notifyEnabled).labelsHidden().tint(Theme.accent)
                    }
                    .padding(14)
                    .background(Theme.text.opacity(0.04), in: RoundedRectangle(cornerRadius: 16))
                    .overlay(RoundedRectangle(cornerRadius: 16).stroke(Theme.border))

                    Button(action: save) {
                        Text(busy ? "Сохранение…" : "Сохранить и проверить")
                            .font(.system(size: 15, weight: .heavy))
                            .foregroundStyle(Color(red: 42 / 255, green: 16 / 255, blue: 32 / 255))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(Theme.accent, in: Capsule())
                    }
                    .disabled(busy)

                    if let message {
                        Text(message).foregroundStyle(Theme.warn).multilineTextAlignment(.center).frame(maxWidth: .infinity)
                    }

                    Text("Установка: откройте Forecast.xcodeproj в Xcode, выберите свой Team и iPhone, нажмите Run. App Store не нужен.")
                        .font(.system(size: 12))
                        .foregroundStyle(Theme.muted)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 8)
                }
                .padding(16)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Настройки")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Закрыть") { dismiss() }.foregroundStyle(Theme.accent)
                }
            }
            .onAppear { draft = store.settings }
        }
        .preferredColorScheme(.dark)
    }

    private func field(_ label: String, @ViewBuilder input: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label.uppercased()).font(.system(size: 12)).foregroundStyle(Theme.muted)
            input()
                .padding(12)
                .foregroundStyle(Theme.text)
                .background(Color(red: 20 / 255, green: 10 / 255, blue: 28 / 255).opacity(0.55), in: RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.border))
        }
    }

    private func save() {
        busy = true
        message = nil
        Task {
            defer { busy = false }
            do {
                try await draft.trimmed.client.testConnection()
                store.save(draft)
                if draft.notifyEnabled {
                    do {
                        try await push.sync(with: store.settings)
                        message = "Сохранено. Пуш при score > 35 включён."
                    } catch {
                        message = "Сохранено. Разрешите уведомления и проверьте, что запущено на телефоне, не в симуляторе."
                    }
                } else {
                    try? await push.sync(with: store.settings)
                    message = "Сохранено. Уведомления выключены."
                }
            } catch {
                message = error.localizedDescription
            }
        }
    }
}
