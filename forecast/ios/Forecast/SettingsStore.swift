import Foundation

struct AppSettings: Codable, Equatable {
    var baseURL: String
    var username: String
    var password: String
    var notifyEnabled: Bool

    static let `default` = AppSettings(
        baseURL: "http://127.0.0.1:8000",
        username: "admin",
        password: "",
        notifyEnabled: false
    )

    var trimmed: AppSettings {
        var copy = self
        while copy.baseURL.hasSuffix("/") { copy.baseURL.removeLast() }
        copy.username = copy.username.trimmingCharacters(in: .whitespaces)
        return copy
    }

    var client: APIClient {
        let t = trimmed
        return APIClient(baseURL: t.baseURL, username: t.username, password: t.password)
    }
}

@MainActor
final class SettingsStore: ObservableObject {
    @Published var settings: AppSettings

    private let key = "forecast.settings.v1"

    init() {
        if let data = UserDefaults.standard.data(forKey: key),
           let decoded = try? JSONDecoder().decode(AppSettings.self, from: data) {
            settings = decoded.trimmed
        } else {
            settings = .default
        }
    }

    func save(_ next: AppSettings) {
        let trimmed = next.trimmed
        settings = trimmed
        if let data = try? JSONEncoder().encode(trimmed) {
            UserDefaults.standard.set(data, forKey: key)
        }
    }
}
