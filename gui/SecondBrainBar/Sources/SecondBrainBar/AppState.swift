import Foundation

/// The derived state the Python side writes to `.state.json`: the authoritative
/// staged set, per-model build cost, count of built pages, and whether a
/// reviewed grouping has drifted from staging. The app renders this rather than
/// recomputing it.
struct AppState: Decodable {
    let staged: [StagedSource]
    let builtCount: Int
    let costs: [String: Double]
    let stale: Bool

    private enum CodingKeys: String, CodingKey {
        case staged
        case stale
        case costs
        case builtCount = "built_count"
    }

    private struct RawStaged: Decodable {
        let rel: String
        let bytes: Int64
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        staged = try container.decode([RawStaged].self, forKey: .staged).map { raw in
            let stem = ((raw.rel as NSString).lastPathComponent as NSString).deletingPathExtension
            return StagedSource(id: raw.rel, displayName: stem, bytes: raw.bytes)
        }
        builtCount = try container.decode(Int.self, forKey: .builtCount)
        costs = try container.decode([String: Double].self, forKey: .costs)
        stale = try container.decode(Bool.self, forKey: .stale)
    }

    static func load(from url: URL) -> AppState? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(AppState.self, from: data)
    }
}
