import SwiftUI

struct SetupCardView: View {
    let setup: Setup

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Text(setup.symbol ?? "—")
                    .font(.system(size: 18, weight: .heavy))
                    .foregroundStyle(Theme.accent)
                sideBadge(setup.direction ?? "—", color: dirColor)
                if setup.hot {
                    Text("ВЫГОДНО")
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundStyle(Color(red: 1, green: 214 / 255, blue: 236 / 255))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(Theme.accent.opacity(0.22), in: Capsule())
                }
                Spacer()
                Text(Theme.num(setup.score, digits: 1))
                    .font(.system(size: 22, weight: .heavy))
                    .foregroundStyle(Theme.accent2)
            }
            if let name = setup.stockName, !name.isEmpty {
                Text(name).font(.system(size: 12)).foregroundStyle(Theme.muted)
            }
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                field("Паттерн", setup.pattern ?? "—")
                field("R:R", Theme.num(setup.riskReward, digits: 2))
                field("Вход", Theme.num(setup.entry), mono: true)
                field("Стоп", Theme.num(setup.stop), mono: true, color: Theme.short)
                field("TP1", Theme.num(setup.target1), mono: true, color: Theme.long)
                field("TP2", Theme.num(setup.target2), mono: true, color: Theme.long)
            }
            if let why = setup.whySelected, !why.isEmpty {
                Text(why).font(.system(size: 13)).foregroundStyle(Theme.muted)
            }
        }
        .padding(14)
        .background(setup.hot ? Color(red: 58 / 255, green: 30 / 255, blue: 72 / 255).opacity(0.95) : Theme.card, in: RoundedRectangle(cornerRadius: 20))
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(setup.hot ? Theme.accent.opacity(0.65) : Theme.border))
    }

    private var dirColor: Color {
        switch setup.direction {
        case "Long": return Theme.long
        case "Short": return Theme.short
        default: return Theme.muted
        }
    }
}

struct TradeCardView: View {
    let trade: PaperTrade

    var body: some View {
        let open = trade.status == "open"
        let status = open ? "OPEN" : (trade.win == true ? "WIN" : "LOSS")
        let r = trade.r
        let rText = r.map { "\($0 > 0 ? "+" : "")\(Theme.num($0, digits: 2))R" } ?? "—"
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Text(trade.symbol ?? "—").font(.system(size: 18, weight: .heavy)).foregroundStyle(Theme.accent)
                sideBadge(trade.side ?? "—", color: trade.side == "Long" ? Theme.long : Theme.short)
                Text(status).font(.system(size: 11, weight: .heavy)).foregroundStyle(Theme.muted)
                Spacer()
                Text(rText)
                    .font(.system(size: 20, weight: .heavy))
                    .foregroundStyle((r ?? 0) > 0 ? Theme.long : ((r ?? 0) < 0 ? Theme.short : Theme.muted))
            }
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                field("Вход", Theme.num(trade.entry), mono: true)
                field("Сейчас", Theme.num(trade.lastPrice), mono: true)
                field("Стоп", Theme.num(trade.stop), mono: true, color: Theme.short)
                field("TP", Theme.num(trade.tp), mono: true, color: Theme.long)
            }
            if !open, let reason = trade.reason, !reason.isEmpty {
                Text(reason).font(.system(size: 13)).foregroundStyle(Theme.muted)
            }
        }
        .padding(14)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 20))
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(trade.win == true ? Theme.accent.opacity(0.65) : Theme.border))
    }
}

private func sideBadge(_ text: String, color: Color) -> some View {
    Text(text)
        .font(.system(size: 12, weight: .heavy))
        .foregroundStyle(color)
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(color.opacity(0.16), in: Capsule())
}

private func field(_ label: String, _ value: String, mono: Bool = false, color: Color = Theme.text) -> some View {
    VStack(alignment: .leading, spacing: 4) {
        Text(label.uppercased()).font(.system(size: 10)).tracking(0.6).foregroundStyle(Theme.muted)
        Text(value)
            .font(mono ? .system(size: 14, design: .monospaced) : .system(size: 14))
            .foregroundStyle(color)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
}
