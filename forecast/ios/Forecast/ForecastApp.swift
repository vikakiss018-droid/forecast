import SwiftUI

@main
struct ForecastApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var settings = SettingsStore()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(settings)
                .environmentObject(PushManager.shared)
                .preferredColorScheme(.dark)
                .background(Theme.bg.ignoresSafeArea())
        }
    }
}
