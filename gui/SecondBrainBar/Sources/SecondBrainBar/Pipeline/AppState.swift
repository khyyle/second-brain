import Foundation

/// The derived state returned by GET /state: the authoritative
/// staged set, estimated build cost, count of built pages, needs-review and
/// skipped counts, whether a reviewed grouping has drifted from staging, and
/// why each failed file failed. The app renders this rather than recomputing it.
struct AppState: Decodable {
    let staged: [StagedSource]
    let builtCount: Int
    /// What building everything staged would cost with the chosen model, or nil when
    /// that model has no known price.
    let estimatedCostUSD: Double?
    let stale: Bool
    let needsReviewCount: Int
    let skippedCount: Int
    /// Absolute path of a dropped file to why its last ingest failed.
    let ingestFailures: [String: String]
    let generatedAt: String
    let pendingTriage: [String]

    private enum CodingKeys: String, CodingKey {
        case staged
        case stale
        case estimatedCostUSD = "estimated_cost_usd"
        case builtCount = "built_count"
        case needsReviewCount = "needs_review"
        case skippedCount = "skipped"
        case ingestFailures = "ingest_failures"
        case generatedAt = "generated_at"
        case pendingTriage = "pending_triage"
    }

    private struct RawStaged: Decodable {
        let rel: String
        let estimated_cost_usd: Double?
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
                estimatedCostUSD: raw.estimated_cost_usd,
                deferReason: raw.defer_reason,
                canRetry: raw.retryable ?? false
            )
        }
        builtCount = try container.decode(Int.self, forKey: .builtCount)
        estimatedCostUSD = try container.decodeIfPresent(Double.self, forKey: .estimatedCostUSD)
        stale = try container.decode(Bool.self, forKey: .stale)
        needsReviewCount = try container.decodeIfPresent(Int.self, forKey: .needsReviewCount) ?? 0
        skippedCount = try container.decodeIfPresent(Int.self, forKey: .skippedCount) ?? 0
        ingestFailures = try container.decodeIfPresent(
            [String: String].self, forKey: .ingestFailures
        ) ?? [:]
        generatedAt = try container.decodeIfPresent(String.self, forKey: .generatedAt) ?? ""
        pendingTriage = try container.decodeIfPresent([String].self, forKey: .pendingTriage) ?? []
    }


}

/// A raw source that has been ingested and passed triage but is not yet
/// compiled into the wiki.
struct StagedSource: Identifiable, Hashable {
    let id: String          // path relative to raw/
    let displayName: String
    /// Estimated cost of building this source on its own, or nil when unknown.
    let estimatedCostUSD: Double?
    var deferReason: String? = nil
    /// Whether a set-aside source could build on a retry. Too-large sources
    /// can't; only splitting the file helps.
    var canRetry = false
}
