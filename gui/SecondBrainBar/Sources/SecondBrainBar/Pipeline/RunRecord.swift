import Foundation

/// The two pipeline stages that record how they ended.
enum PipelineStage: String, CaseIterable {
    case ingest
    case compile
}

/// How the last ingest and the last build each ended, read from the
/// `.last-run.json` the Python CLI writes when a run finishes. Each stage keeps
/// its own entry, so a clean ingest never hides a failed build.
struct RunRecord: Decodable, Equatable {
    let ingest: StageRun?
    let compile: StageRun?

    private enum CodingKeys: String, CodingKey {
        case ingest
        case compile
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        // An entry this version can't read must not hide the other stage.
        ingest = try? container.decodeIfPresent(StageRun.self, forKey: .ingest)
        compile = try? container.decodeIfPresent(StageRun.self, forKey: .compile)
    }

    func run(for stage: PipelineStage) -> StageRun? {
        switch stage {
        case .ingest: return ingest
        case .compile: return compile
        }
    }

    static func load(from url: URL) -> RunRecord? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(RunRecord.self, from: data)
    }
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
