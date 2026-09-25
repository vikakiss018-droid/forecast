import SwiftUI

enum Theme {
    static let bg = Color(red: 20 / 255, green: 10 / 255, blue: 24 / 255)
    static let card = Color(red: 42 / 255, green: 22 / 255, blue: 52 / 255).opacity(0.92)
    static let border = Color(red: 255 / 255, green: 182 / 255, blue: 220 / 255).opacity(0.28)
    static let text = Color(red: 255 / 255, green: 240 / 255, blue: 248 / 255)
    static let muted = Color(red: 201 / 255, green: 168 / 255, blue: 212 / 255)
    static let accent = Color(red: 255 / 255, green: 158 / 255, blue: 207 / 255)
    static let accent2 = Color(red: 233 / 255, green: 184 / 255, blue: 255 / 255)
    static let long = Color(red: 184 / 255, green: 245 / 255, blue: 212 / 255)
    static let short = Color(red: 255 / 255, green: 158 / 255, blue: 184 / 255)
    static let warn = Color(red: 255 / 255, green: 216 / 255, blue: 154 / 255)

    static func num(_ value: Double?, digits: Int = 4) -> String {
        guard let value, value.isFinite else { return "—" }
        if abs(value) >= 1000 {
            return value.formatted(.number.precision(.fractionLength(0...2)).locale(Locale(identifier: "ru_RU")))
        }
        return String(format: "%.\(digits)f", value)
    }

    static func time(_ iso: String?) -> String {
        guard let iso, !iso.isEmpty else { return "—" }
        let withFrac = ISO8601DateFormatter()
        withFrac.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        let date = withFrac.date(from: iso) ?? plain.date(from: iso.replacingOccurrences(of: "Z", with: "+00:00"))
        guard let date else { return "—" }
        let out = DateFormatter()
        out.locale = Locale(identifier: "ru_RU")
        out.dateFormat = "dd.MM HH:mm"
        return out.string(from: date)
    }
}
