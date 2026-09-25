import Foundation

enum APIError: LocalizedError {
    case notConfigured
    case unauthorized
    case status(Int)
    case decode

    var errorDescription: String? {
        switch self {
        case .notConfigured: return "Укажите адрес сервера и пароль в настройках"
        case .unauthorized: return "Неверный логин или пароль"
        case .status(let code): return "Сервер ответил \(code)"
        case .decode: return "Не удалось разобрать ответ сервера"
        }
    }
}

struct APIClient {
    var baseURL: String
    var username: String
    var password: String

    private var root: URL? {
        URL(string: baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")))
    }

    func fetchFeed(_ kind: FeedKind) async throws -> SetupsResponse {
        let compact: String
        let fallback: String
        switch kind {
        case .scan:
            compact = "/m/api/setups"
            fallback = "/scanner/json"
        case .swing:
            compact = "/m/api/swing"
            fallback = "/swing/json"
        case .stocks:
            compact = "/m/api/stocks"
            fallback = "/stocks/json"
        case .paper:
            throw APIError.notConfigured
        }
        do {
            return try await get(compact)
        } catch APIError.status(let code) where [404, 405].contains(code) {
            return try await get(fallback)
        } catch APIError.decode {
            return try await get(fallback)
        }
    }

    func fetchPaper() async throws -> PaperResponse {
        do {
            return try await get("/m/api/paper")
        } catch APIError.status(let code) where [404, 405].contains(code) {
            return try await get("/paper/json")
        } catch APIError.decode {
            return try await get("/paper/json")
        }
    }

    func testConnection() async throws {
        _ = try await fetchFeed(.scan)
    }

    func startScan(kind: FeedKind) async throws -> ScanRunResult {
        try await postJSON("/m/api/scan/run", body: ["kind": kind.rawValue])
    }

    func fetchProgress(kind: FeedKind) async throws -> ScanProgress {
        try await get("/m/api/scan/progress?kind=\(kind.rawValue)")
    }

    func registerAPNs(token: String) async throws {
        try await post("/m/api/apns/register", body: ["token": token, "platform": "ios"])
    }

    func unregisterAPNs(token: String) async throws {
        try await post("/m/api/apns/unregister", body: ["token": token])
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        let data = try await request(path, method: "GET", body: nil)
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decode
        }
    }

    private func postJSON<T: Decodable>(_ path: String, body: [String: String]) async throws -> T {
        let data = try await request(path, method: "POST", body: body)
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decode
        }
    }

    private func post(_ path: String, body: [String: String]) async throws {
        _ = try await request(path, method: "POST", body: body)
    }

    private func request(_ path: String, method: String, body: [String: String]?) async throws -> Data {
        guard let root, !password.isEmpty else { throw APIError.notConfigured }
        guard let url = URL(string: path, relativeTo: root) else { throw APIError.notConfigured }
        var req = URLRequest(url: url, timeoutInterval: 8)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        let raw = "\(username):\(password)"
        let basic = Data(raw.utf8).base64EncodedString()
        req.setValue("Basic \(basic)", forHTTPHeaderField: "Authorization")
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 401 { throw APIError.unauthorized }
        if code < 200 || code >= 300 { throw APIError.status(code) }
        return data
    }
}
