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
    @Published var removalError: String?
    /// Stale backend snapshots must not restore a locally removed source.
    @Published private var removingSources: Set<String> = []
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

    /// Sources the next build compiles, in the order it compiles them.
    var stagedForBuild: [StagedSource] {
        (state?.staged ?? []).filter { $0.deferReason == nil && !isRemoving($0.id) }
    }
    /// Sources an earlier build set aside; builds skip them until they're retried.
    var setAside: [StagedSource] {
        (state?.staged ?? []).filter { $0.deferReason != nil && !isRemoving($0.id) }
    }
    var pendingTriage: [String] { state?.pendingTriage ?? [] }
    var builtCount: Int { state?.builtCount ?? 0 }
    var needsReviewCount: Int { state?.needsReviewCount ?? 0 }
    var skippedCount: Int { state?.skippedCount ?? 0 }
    var stateStamp: String { state?.generatedAt ?? "" }
    var stale: Bool { state?.stale ?? false }
    var hasState: Bool { state != nil }
    var buildingSources: Set<String> {
        guard isCompiling, let status else { return [] }
        return Set(status.currentSources)
    }

    /// The heartbeat of the build in progress, for the active row's live details.
    var buildProgress: PipelineStatus? { isCompiling ? status : nil }

    /// The heartbeat of triage in progress, which names the chat being sorted.
    var triageProgress: PipelineStatus? { isActive && status?.phase == "triage" ? status : nil }

    func cost(for model: String) -> Double { state?.costs[model] ?? 0 }

    func ingestFailureReason(for path: String) -> String? { state?.ingestFailures[path] }

    func isRemoving(_ path: String) -> Bool { removingSources.contains(path) }

    func removeStagedSource(_ path: String) {
        let config = config
        remove(path, url: config.rawRoot.appending(path: path)) {
            ManifestMutator.removeStagedSource(config: config, rawRel: path)
        }
    }

    func removeDroppedSource(_ path: String) {
        let config = config
        remove(path, url: URL(fileURLWithPath: path)) {
            ManifestMutator.removeSource(config: config, filePath: path)
        }
    }

    private func remove(
        _ path: String,
        url: URL,
        action: @escaping () -> PipelineRunner.ManagedCommandResult
    ) {
        guard !isRemoving(path) else { return }
        removingSources.insert(path)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = action()
            DispatchQueue.main.async {
                guard let self else { return }
                if !result.succeeded {
                    if FileManager.default.fileExists(atPath: url.path) {
                        self.removingSources.remove(path)
                    }
                    self.removalError = result.failureReason ?? result.message ?? "Try again."
                }
                self.refresh()
            }
        }
    }

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

    func dismiss(_ notice: RunNotice) {
        dismissedRuns[notice.stage.rawValue] = notice.run.finishedAt
        UserDefaults.standard.set(dismissedRuns, forKey: Self.dismissedRunsKey)
    }

    private func refresh() {
        status = PipelineStatus.read(from: config.statusFile)
        state = AppState.load(from: config.stateFile)
        if let state {
            let staged = Set(state.staged.map(\.id))
            removingSources = removingSources.filter { path in
                path.hasPrefix("/")
                    ? FileManager.default.fileExists(atPath: path)
                    : staged.contains(path)
            }
        }
        let record = RunRecord.load(from: config.runRecordFile)
        if record != runRecord { runRecord = record }
        if !isCompiling { stopping = false }
    }
}
