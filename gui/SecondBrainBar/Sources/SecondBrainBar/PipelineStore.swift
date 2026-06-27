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

    private let config: AppConfig
    private var cancellable: AnyCancellable?

    init(config: AppConfig) {
        self.config = config
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
    var stale: Bool { state?.stale ?? false }
    var hasState: Bool { state != nil }

    func cost(for model: String) -> Double { state?.costs[model] ?? 0 }

    /// Ask the running compile to stop, shown as a disabled "Stopping" until
    /// the heartbeat reports the run has ended.
    func requestStop() {
        stopping = true
        PipelineRunner.requestStop(vaultRoot: config.vaultRoot)
    }

    private func refresh() {
        status = PipelineStatus.read(from: config.statusFile)
        state = AppState.load(from: config.stateFile)
        if !isCompiling { stopping = false }
    }
}
