import Foundation

struct JobPage: Decodable { let items: [JobResponse] }
struct JobDetail: Decodable { let job: JobResponse; let progress: JobProgress }
struct JobResponse: Decodable, Identifiable {
    let id: String
    let kind: String
    let lane: String
    let status: String
    let error: String
    let result: JobResult
    let cancel_requested: Bool
    let created_at: String
    let started_at: String?
    let finished_at: String?
    let heartbeat_at: String?
    var isActive: Bool { status == "queued" || status == "running" }
    var stage: PipelineStage { lane == "ingest" ? .ingest : .compile }
    var stageRun: StageRun {
        let outcome: StageRun.Outcome
        switch status {
        case "succeeded": outcome = StageRun.Outcome(rawValue: result.outcome ?? "") ?? .ok
        case "cancelled": outcome = .stopped
        default: outcome = .failed
        }
        return StageRun(outcome: outcome, reason: error.isEmpty ? (result.reason ?? "") : error,
                        finishedAt: finished_at ?? id)
    }
}
struct JobResult: Decodable {
    let outcome: String?
    let reason: String?
}
struct JobProgress: Decodable {
    let phase: String?
    let current: Int?
    let total: Int?
    let cost_usd: Double?
    let current_sources: [String]?
    let queued_sources: [String]?
    let current_started_at: String?
    let current_cost_usd: Double?
    let current_activity: String?
}
