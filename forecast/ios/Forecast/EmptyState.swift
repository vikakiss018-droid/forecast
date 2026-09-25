import SwiftUI

struct OfflineBanner: View {
    let text: String
    let actionTitle: String?
    let action: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(text)
                .font(.system(size: 14))
                .foregroundStyle(Theme.warn)
                .fixedSize(horizontal: false, vertical: true)
            if let actionTitle, let action {
                Button(action: action) {
                    Text(actionTitle)
                        .font(.system(size: 13, weight: .heavy))
                        .foregroundStyle(Color(red: 42 / 255, green: 16 / 255, blue: 32 / 255))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(Theme.accent, in: Capsule())
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.warn.opacity(0.08), in: RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Theme.warn.opacity(0.35)))
    }
}

struct EmptyFeedCard: View {
    let title: String
    let subtitle: String

    var body: some View {
        VStack(spacing: 10) {
            Text(title)
                .font(.system(size: 16, weight: .heavy))
                .foregroundStyle(Theme.text)
            Text(subtitle)
                .font(.system(size: 13))
                .foregroundStyle(Theme.muted)
                .multilineTextAlignment(.center)
        }
        .padding(28)
        .frame(maxWidth: .infinity)
        .overlay(
            RoundedRectangle(cornerRadius: 20)
                .stroke(style: StrokeStyle(lineWidth: 1, dash: [6]))
                .foregroundStyle(Theme.border)
        )
    }
}

struct SkeletonSetupCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                capsule(width: 92, height: 18)
                capsule(width: 52, height: 18)
                Spacer()
                capsule(width: 36, height: 22)
            }
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                mini("Паттерн")
                mini("R:R")
                mini("Вход")
                mini("Стоп")
                mini("TP1")
                mini("TP2")
            }
        }
        .padding(14)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 20))
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(Theme.border))
        .opacity(0.55)
    }

    private func mini(_ label: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label.uppercased()).font(.system(size: 10)).foregroundStyle(Theme.muted)
            capsule(width: 72, height: 12)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func capsule(width: CGFloat, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 6)
            .fill(Theme.text.opacity(0.12))
            .frame(width: width, height: height)
    }
}
