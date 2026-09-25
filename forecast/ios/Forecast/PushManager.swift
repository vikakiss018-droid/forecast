import Foundation
import UIKit
import UserNotifications

@MainActor
final class PushManager: ObservableObject {
    static let shared = PushManager()

    @Published var deviceToken: String?
    @Published var registrationError: String?

    func requestPermissionAndRegister() async -> Bool {
        do {
            let granted = try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound, .badge])
            guard granted else { return false }
            UIApplication.shared.registerForRemoteNotifications()
            return true
        } catch {
            registrationError = error.localizedDescription
            return false
        }
    }

    func didRegister(deviceToken: Data) {
        self.deviceToken = deviceToken.map { String(format: "%02.2hhx", $0) }.joined()
    }

    func sync(with settings: AppSettings) async throws {
        guard settings.notifyEnabled else {
            if let token = deviceToken {
                try? await settings.client.unregisterAPNs(token: token)
            }
            return
        }
        let ok = await requestPermissionAndRegister()
        guard ok else { throw APIError.status(0) }
        // Token may arrive a moment later.
        for _ in 0..<20 {
            if let token = deviceToken {
                try await settings.client.registerAPNs(token: token)
                return
            }
            try await Task.sleep(nanoseconds: 150_000_000)
        }
        throw APIError.decode
    }
}
