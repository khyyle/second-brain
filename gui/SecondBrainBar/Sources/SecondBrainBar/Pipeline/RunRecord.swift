import Foundation

/// The two pipeline stages that record how they ended.
enum PipelineStage: String, CaseIterable {
    case ingest
    case compile
}

/// Latest completed ingest and build outcomes from durable jobs.
struct RunRecord: Equatable {
    let ingest: StageRun?
    let compile: StageRun?
    func run(for stage: PipelineStage) -> StageRun? { stage == .ingest ? ingest : compile }
}

/// One stage's ending. `reason` is a plain sentence Python wrote, shown as-is
/// so the app and the CLI never word the same failure differently.
struct StageRun: Decodable, Equatable {
    enum Outcome: String, Decodable {
        case ok
        case partial
        case capped
        case failed
        case stopped
    }

    let outcome: Outcome
    let reason: String
    /// Identifies this run; the next run of the stage writes a new value.
    let finishedAt: String

    private enum CodingKeys: String, CodingKey {
        case outcome
        case reason
        case finishedAt = "finished_at"
    }

    /// A stopped run is the user's own doing, so only these need a notice.
    var needsAttention: Bool {
        switch outcome {
        case .partial, .capped, .failed: return true
        case .ok, .stopped: return false
        }
    }
}

/// A run that ended badly and that the user has not dismissed yet.
struct RunNotice: Equatable, Identifiable {
    let stage: PipelineStage
    let run: StageRun

    var id: String { "\(stage.rawValue):\(run.finishedAt)" }
}
