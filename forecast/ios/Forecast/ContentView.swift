import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var settings: SettingsStore
    @State private var tab: FeedKind = .scan
    @State private var showSettings = false

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()
            VStack(spacing: 0) {
                Group {
                    if tab == .paper {
                        PaperView(onOpenSettings: { showSettings = true })
                    } else {
                        FeedView(kind: tab, onOpenSettings: { showSettings = true })
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                tabBar
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .sheet(isPresented: $showSettings) {
            SettingsView()
                .environmentObject(settings)
                .environmentObject(PushManager.shared)
        }
    }

    private var tabBar: some View {
        HStack(spacing: 4) {
            ForEach([FeedKind.scan, .swing, .stocks, .paper]) { item in
                Button {
                    tab = item
                } label: {
                    Text(item.title)
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundStyle(tab == item ? Color(red: 42 / 255, green: 16 / 255, blue: 32 / 255) : Theme.muted)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(tab == item ? Theme.accent : Color.clear, in: RoundedRectangle(cornerRadius: 12))
                }
            }
        }
        .padding(.horizontal, 8)
        .padding(.top, 8)
        .padding(.bottom, 8)
        .background(Color(red: 20 / 255, green: 10 / 255, blue: 24 / 255).opacity(0.96))
        .overlay(alignment: .top) { Theme.border.frame(height: 1) }
    }
}
