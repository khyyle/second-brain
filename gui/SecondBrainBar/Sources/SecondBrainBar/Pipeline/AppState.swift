import Foundation

/// The derived state the Python side writes to `.state.json`: the authoritative
/// staged set, per-model build cost, count of built pages, needs-review and
/// skipped counts, whether a reviewed grouping has drifted from staging, and
/// why each failed file failed. The app renders this rather than recomputing it.
struct AppState: Decodable {
    let staged: [StagedSource]
    let builtCount: Int
    let costs: [String: Double]
    let stale: Bool
    let needsReviewCount: Int
    let skippedCount: Int
    /// Absolute path of a dropped file to why its last ingest failed.
    let ingestFailures: [String: String]
    let generatedAt: String

    private enum CodingKeys: String, CodingKey {
        case staged
        case stale
        case costs
        case builtCount = "built_count"
        case needsReviewCount = "needs_review"
        case skippedCount = "skipped"
        case ingestFailures = "ingest_failures"
        case generatedAt = "generated_at"
    }

    private struct RawStaged: Decodable {
        let rel: String
        let bytes: Int64
        let defer_reason: String?
        let retryable: Bool?
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        staged = try container.decode([RawStaged].self, forKey: .staged).map { raw in
            let stem = ((raw.rel as NSString).lastPathComponent as NSString).deletingPathExtension
            return StagedSource(
                id: raw.rel,
                displayName: stem,
                bytes: raw.bytes,
                deferReason: raw.defer_reason,
                canRetry: raw.retryable ?? false
            )
        }
        builtCount = try container.decode(Int.self, forKey: .builtCount)
        costs = try container.decode([String: Double].self, forKey: .costs)
        stale = try container.decode(Bool.self, forKey: .stale)
        needsReviewCount = try container.decodeIfPresent(Int.self, forKey: .needsReviewCount) ?? 0
        skippedCount = try container.decodeIfPresent(Int.self, forKey: .skippedCount) ?? 0
        ingestFailures = try container.decodeIfPresent(
            [String: String].self, forKey: .ingestFailures
        ) ?? [:]
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt) ?? ""
    }

    static func load(from url: URL) -> AppState? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(AppState.self, from: data)
    }
}

/// A raw source that has been ingested and passed triage but is not yet
/// compiled into the wiki.
struct StagedSource: Identifiable, Hashable {
    let id: String          // path relative to raw/
    let displayName: String
    let bytes: Int64
    var deferReason: String? = nil
    /// Whether a set-aside source could build on a retry. Too-large sources
    /// can't; only splitting the file helps.
    var canRetry = false

    var sizeText: String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }
}
