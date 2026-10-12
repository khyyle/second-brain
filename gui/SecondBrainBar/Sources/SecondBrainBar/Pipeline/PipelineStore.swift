import Foundation
import Combine

/// Shared live pipeline state: the heartbeat status and the facts derived from
/// it (which phase is running, whether the UI is locked during a compile, and
/// the cooperative stop state). One instance is injected into every tab so
/// these reads are defined once rather than recomputed per view.
final class PipelineStore: ObservableObject {
    @Published private(set) var status: PipelineStatus?
    @Published private(set) var state: AppState?
    @Published private(set) var stopping = false
    @Published private(set) var runRecord: RunRecord?
    /// The `finishedAt` of the last run the user dismissed, per stage. Kept by
    /// the app so it never writes to the pipeline's own record.
    @Published private var dismissedRuns: [String: String]

    private static let dismissedRunsKey = "dismissedRunOutcomes"

    private let config: AppConfig
    private var cancellable: AnyCancellable?

    init(config: AppConfig) {
        self.config = config
        dismissedRuns = UserDefaults.standard
            .dictionary(forKey: Self.dismissedRunsKey) as? [String: String] ?? [:]
        refresh()
        // Generate the derived-state file if a run hasn't written one yet, so a
        // fresh launch renders the staged set without waiting for the next run.
        if state == nil, let repo = config.repoDir {
            PipelineRunner.runManaged(repoDir: repo, command: "state")
        }
        cancellable = Timer.publish(every: 1, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in self?.refresh() }
    }

    var isActive: Bool { status?.isActive == true }
    var isCompiling: Bool { isActive && status?.phase == "compile" }
    var isGrouping: Bool { isActive && status?.phase == "cluster" }

    /// Editing the plan, staging, or wiki is blocked while a compile runs.
    var locked: Bool { isCompiling }

    var staged: [StagedSource] { state?.staged ?? [] }
    var builtCount: Int { state?.builtCount ?? 0 }
    var needsReviewCount: Int { state?.needsReviewCount ?? 0 }
    var skippedCount: Int { state?.skippedCount ?? 0 }
    var stateStamp: String { state?.generatedAt ?? "" }
    var stale: Bool { state?.stale ?? false }
    var hasState: Bool { state != nil }

    func cost(for model: String) -> Double { state?.costs[model] ?? 0 }

    /// Ask the running compile to stop, shown as a disabled "Stopping" until
    /// the heartbeat reports the run has ended.
    func requestStop() {
        stopping = true
        PipelineRunner.requestStop(vaultRoot: config.vaultRoot)
    }

    /// The stage whose run is in progress, if any. Grouping prepares a build.
    var activeStage: PipelineStage? {
        guard isActive, let phase = status?.phase else { return nil }
        switch phase {
        case "compile", "cluster": return .compile
        case "ingest", "triage": return .ingest
        default: return nil
        }
    }

    /// The stage's last run, if it ended badly and hasn't been dismissed. A
    /// running stage is about to replace its record, so its old ending is held
    /// back.
    func notice(for stage: PipelineStage) -> RunNotice? {
        guard stage != activeStage,
              let run = runRecord?.run(for: stage),
              run.needsAttention,
              dismissedRuns[stage.rawValue] != run.finishedAt
        else { return nil }
        return RunNotice(stage: stage, run: run)
    }

    /// The single notice the banner shows. The build wins over ingest because
    /// it is the stage that spends money.
    var runNotice: RunNotice? { notice(for: .compile) ?? notice(for: .ingest) }

    func dismiss(_ notice: RunNotice) {
        dismissedRuns[notice.stage.rawValue] = notice.run.finishedAt
        UserDefaults.standard.set(dismissedRuns, forKey: Self.dismissedRunsKey)
    }

    private func refresh() {
        status = PipelineStatus.read(from: config.statusFile)
        state = AppState.load(from: config.stateFile)
        let record = RunRecord.load(from: config.runRecordFile)
        if record != runRecord { runRecord = record }
        if !isCompiling { stopping = false }
    }
}
