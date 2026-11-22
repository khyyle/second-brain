import Foundation

/// Presentation of one durable job's progress
struct PipelineStatus: Identifiable {
    let id: String
    let running: Bool
    let queued: Bool
    let kind: String
    let phase: String
    let current: Int
    let total: Int
    let updatedAt: Date
    let startedAt: Date
    let costUSD: Double
    let currentSources: [String]
    let queuedSources: [String]
    let currentStartedAt: Date?
    let currentCostUSD: Double
    let currentActivity: String
    var isActive: Bool { running }
    var elapsed: TimeInterval { max(0, Date().timeIntervalSince(startedAt)) }

    init(_ detail: JobDetail) {
        let job = detail.job, p = detail.progress
        id = job.id
        queued = job.status == "queued"
        kind = job.kind
        running = job.isActive
        phase = (p.phase == "grouping" ? "cluster" : p.phase) ?? (job.kind == "build" ? "compile" : job.kind == "preview" ? "cluster" : job.kind)
        current = p.current ?? 0
        total = p.total ?? 0
        updatedAt = backendDate(job.heartbeat_at) ?? Date()
        startedAt = backendDate(job.started_at ?? job.created_at) ?? Date()
        costUSD = p.cost_usd ?? 0
        currentSources = p.current_sources ?? []
        queuedSources = p.queued_sources ?? []
        currentStartedAt = backendDate(p.current_started_at)
        currentCostUSD = p.current_cost_usd ?? 0
        currentActivity = job.status == "queued" ? "Queued" : p.current_activity ?? ""
    }

    /// Local display time derives from the server start timestamp, so reconnects
    /// and sleep do not reset it. No heartbeat or HTTP request advances the clock.
    func footerDetail(at now: Date) -> String {
        if queued {
            switch kind {
            case "build": return "Build queued"
            case "preview": return "Grouping queued"
            case "index": return "Index update queued"
            default: return "Ingest queued"
            }
        }
        let verb: String
        switch phase {
        case "compile": verb = "Building"
        case "cluster": verb = "Grouping"
        case "triage": verb = "Triaging"
        case "index": verb = "Indexing"
        default: verb = "Ingesting"
        }
        var parts = [total > 0 ? "\(verb) \(min(current + 1, total))/\(total)" : verb]
        parts.append(formatElapsed(max(0, now.timeIntervalSince(startedAt))))
        if costUSD > 0 { parts.append(String(format: "$%.2f", costUSD)) }
        return parts.joined(separator: " · ")
    }
}
