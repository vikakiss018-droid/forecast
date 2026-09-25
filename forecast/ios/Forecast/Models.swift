import Foundation

enum FeedKind: String, CaseIterable, Identifiable {
    case scan, swing, stocks, paper
    var id: String { rawValue }

    var title: String {
        switch self {
        case .scan: return "Сканер"
        case .swing: return "Среднесрок"
        case .stocks: return "Акции"
        case .paper: return "Симуляция"
        }
    }

    var subtitle: String {
        switch self {
        case .scan: return "Крипто live-скан"
        case .swing: return "Удержание 1–4 недели"
        case .stocks: return "Binance bStocks"
        case .paper: return "Бумажные сделки"
        }
    }

    var runLabel: String {
        switch self {
        case .scan, .swing, .stocks: return "Запустить скан"
        case .paper: return "Обновить цены"
        }
    }
}

struct ScanRunResult: Decodable {
    var ok: Bool?
    var kind: String?
    var started: Bool?
    var busy: Bool?
    var status: String?
}

struct ScanProgress: Decodable {
    var status: String?
    var error: String?
    var progress: Step?

    struct Step: Decodable {
        var current: Double?
        var total: Double?
        var symbol: String?

        enum CodingKeys: String, CodingKey { case current, total, symbol }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            current = Self.num(c, .current)
            total = Self.num(c, .total)
            symbol = try c.decodeIfPresent(String.self, forKey: .symbol)
        }

        private static func num(_ c: KeyedDecodingContainer<CodingKeys>, _ key: CodingKeys) -> Double? {
            if let d = try? c.decodeIfPresent(Double.self, forKey: key) { return d }
            if let i = try? c.decodeIfPresent(Int.self, forKey: key) { return Double(i) }
            return nil
        }
    }

    var isRunning: Bool { (status ?? "") == "running" }

    var label: String {
        let cur = Int(progress?.current ?? 0)
        let tot = Int(progress?.total ?? 0)
        let sym = progress?.symbol ?? ""
        if tot > 0 {
            let pct = min(100, Int((Double(cur) / Double(tot) * 100).rounded()))
            return sym.isEmpty ? "Скан \(cur)/\(tot) · \(pct)%" : "\(sym) · \(cur)/\(tot)"
        }
        if isRunning { return "Скан выполняется…" }
        return ""
    }
}

struct Setup: Identifiable, Decodable {
    var id: String { "\(symbol ?? "")-\(score)-\(entry ?? 0)" }
    var symbol: String?
    var score: Double
    var direction: String?
    var pattern: String?
    var trend: String?
    var probabilityPct: Double?
    var probabilityIsHeuristic: Bool?
    var riskReward: Double?
    var entry: Double?
    var stop: Double?
    var target1: Double?
    var target2: Double?
    var whySelected: String?
    var regime: String?
    var stockName: String?
    var ticker: String?
    var hot: Bool

    enum CodingKeys: String, CodingKey {
        case symbol, score, direction, pattern, trend
        case probabilityPct = "probability_pct"
        case probabilityIsHeuristic = "probability_is_heuristic"
        case riskReward = "risk_reward"
        case entry, stop
        case target1 = "target_1"
        case target2 = "target_2"
        case whySelected = "why_selected"
        case regime
        case stockName = "stock_name"
        case ticker, hot
    }
}

struct SetupsResponse: Decodable {
    var updatedAt: String?
    var timeframe: String?
    var candidatesFound: Int?
    var symbolsScanned: Int?
    var alertMinScore: Double?
    var hotCount: Int?
    var setups: [Setup]

    enum CodingKeys: String, CodingKey {
        case updatedAt = "updated_at"
        case timeframe
        case candidatesFound = "candidates_found"
        case symbolsScanned = "symbols_scanned"
        case alertMinScore = "alert_min_score"
        case hotCount = "hot_count"
        case setups
        case report
        case scanConfig = "scan_config"
        case topSetups = "top_setups"
        case universeCount = "universe_count"
        case universeSize = "universe_size"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if c.contains(.setups) {
            setups = try c.decodeIfPresent([Setup].self, forKey: .setups) ?? []
            updatedAt = try c.decodeIfPresent(String.self, forKey: .updatedAt)
            timeframe = try c.decodeIfPresent(String.self, forKey: .timeframe)
            candidatesFound = try c.decodeIfPresent(Int.self, forKey: .candidatesFound)
            symbolsScanned = try c.decodeIfPresent(Int.self, forKey: .symbolsScanned)
            alertMinScore = try c.decodeIfPresent(Double.self, forKey: .alertMinScore)
            hotCount = try c.decodeIfPresent(Int.self, forKey: .hotCount)
            return
        }
        let report = try c.decodeIfPresent(Report.self, forKey: .report)
        let cfg = try c.decodeIfPresent(ScanConfig.self, forKey: .scanConfig)
        let threshold = (try c.decodeIfPresent(Double.self, forKey: .alertMinScore)) ?? 35
        let rows = report?.topSetups ?? []
        setups = rows.map { $0.normalized(threshold: threshold) }.sorted { $0.score > $1.score }
        updatedAt = (try c.decodeIfPresent(String.self, forKey: .updatedAt)) ?? report?.updatedAt
        timeframe = report?.timeframe ?? cfg?.timeframe
        candidatesFound = report?.candidatesFound
        var scanned = try c.decodeIfPresent(Int.self, forKey: .universeCount)
        if scanned == nil {
            scanned = report?.symbolsScanned
        }
        if scanned == nil {
            scanned = try c.decodeIfPresent(Int.self, forKey: .universeSize)
        }
        symbolsScanned = scanned
        alertMinScore = threshold
        hotCount = setups.filter(\.hot).count
    }

    private struct Report: Decodable {
        var updatedAt: String?
        var timeframe: String?
        var candidatesFound: Int?
        var symbolsScanned: Int?
        var topSetups: [RawSetup]?
        enum CodingKeys: String, CodingKey {
            case updatedAt = "updated_at"
            case timeframe
            case candidatesFound = "candidates_found"
            case symbolsScanned = "symbols_scanned"
            case topSetups = "top_setups"
        }
    }

    private struct ScanConfig: Decodable {
        var timeframe: String?
    }
}

struct RawSetup: Decodable {
    var symbol: String?
    var score: Double?
    var pattern: String?
    var trend: String?
    var whySelected: String?
    var regime: String?
    var stockName: String?
    var ticker: String?
    var setup: Inner?
    var direction: String?

    struct Inner: Decodable {
        var direction: String?
        var trend: String?
        var probabilityPct: Double?
        var probabilityIsHeuristic: Bool?
        var riskReward: Double?
        var entry: Double?
        var stop: Double?
        var target1: Double?
        var target2: Double?
        enum CodingKeys: String, CodingKey {
            case direction, trend
            case probabilityPct = "probability_pct"
            case probabilityIsHeuristic = "probability_is_heuristic"
            case riskReward = "risk_reward"
            case entry, stop
            case target1 = "target_1"
            case target2 = "target_2"
        }
    }

    enum CodingKeys: String, CodingKey {
        case symbol, score, pattern, trend, regime, ticker, setup, direction
        case whySelected = "why_selected"
        case stockName = "stock_name"
    }

    func normalized(threshold: Double) -> Setup {
        let score = score ?? 0
        var dir = (setup?.direction ?? direction ?? "")
        if dir.lowercased() == "long" { dir = "Long" }
        if dir.lowercased() == "short" { dir = "Short" }
        return Setup(
            symbol: symbol,
            score: (score * 10).rounded() / 10,
            direction: dir,
            pattern: pattern,
            trend: trend ?? setup?.trend,
            probabilityPct: setup?.probabilityPct,
            probabilityIsHeuristic: setup?.probabilityIsHeuristic,
            riskReward: setup?.riskReward,
            entry: setup?.entry,
            stop: setup?.stop,
            target1: setup?.target1,
            target2: setup?.target2,
            whySelected: whySelected,
            regime: regime,
            stockName: stockName,
            ticker: ticker,
            hot: score > threshold
        )
    }
}

struct PaperTrade: Identifiable, Decodable {
    var id: String { storedId ?? "\(symbol ?? "")-\(entry ?? 0)-\(status)" }
    var storedId: String?
    var symbol: String?
    var side: String?
    var status: String
    var win: Bool?
    var r: Double?
    var entry: Double?
    var lastPrice: Double?
    var stop: Double?
    var tp: Double?
    var score: Double?
    var reason: String?

    enum CodingKeys: String, CodingKey {
        case storedId = "id"
        case symbol, side, status, win, r, entry, stop, tp, score, reason
        case lastPrice = "last_price"
        case unrealizedR = "unrealized_r"
        case rMultiple = "r_multiple"
        case direction
        case exitReason = "exit_reason"
        case closeReason = "close_reason"
        case target1 = "target_1"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        storedId = try c.decodeIfPresent(String.self, forKey: .storedId)
        symbol = try c.decodeIfPresent(String.self, forKey: .symbol)
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? "closed"
        win = try c.decodeIfPresent(Bool.self, forKey: .win)
        entry = try c.decodeIfPresent(Double.self, forKey: .entry)
        lastPrice = try c.decodeIfPresent(Double.self, forKey: .lastPrice)
        stop = try c.decodeIfPresent(Double.self, forKey: .stop)
        tp = try c.decodeIfPresent(Double.self, forKey: .tp)
        if tp == nil {
            tp = try c.decodeIfPresent(Double.self, forKey: .target1)
        }
        score = try c.decodeIfPresent(Double.self, forKey: .score)
        var side = try c.decodeIfPresent(String.self, forKey: .side) ?? ""
        if side.isEmpty {
            side = try c.decodeIfPresent(String.self, forKey: .direction) ?? ""
        }
        if side.lowercased() == "long" { side = "Long" }
        if side.lowercased() == "short" { side = "Short" }
        self.side = side
        let open = status == "open"
        if let direct = try c.decodeIfPresent(Double.self, forKey: .r) {
            r = direct
        } else if open {
            r = try c.decodeIfPresent(Double.self, forKey: .unrealizedR)
        } else {
            r = try c.decodeIfPresent(Double.self, forKey: .rMultiple)
        }
        var rawReason = try c.decodeIfPresent(String.self, forKey: .reason) ?? ""
        if rawReason.isEmpty {
            rawReason = try c.decodeIfPresent(String.self, forKey: .exitReason) ?? ""
        }
        if rawReason.isEmpty {
            rawReason = try c.decodeIfPresent(String.self, forKey: .closeReason) ?? ""
        }
        let map = ["tp": "TP", "stop": "стоп", "time": "таймаут"]
        reason = open ? "" : (map[rawReason] ?? rawReason)
    }
}

struct PaperSummary: Decodable {
    var updatedAt: String?
    var minScore: Double?
    var open: Int?
    var closed: Int?
    var winRatePct: Double?
    var totalR: Double?
    var pnlUsdt: Double?
    var unrealizedR: Double?

    enum CodingKeys: String, CodingKey {
        case updatedAt = "updated_at"
        case minScore = "min_score"
        case open, closed
        case winRatePct = "win_rate_pct"
        case totalR = "total_r"
        case pnlUsdt = "pnl_usdt"
        case unrealizedR = "unrealized_r"
    }
}

struct PaperResponse: Decodable {
    var summary: PaperSummary
    var trades: [PaperTrade]
}
