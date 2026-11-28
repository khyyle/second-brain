import Foundation
import Combine

/// Tracks backend snapshots with independent progress for concurrent ingest and build jobs.
@MainActor
final class PipelineStore: ObservableObject {
    @Published private(set) var state: AppState?
    @Published private(set) var ingestStatus: PipelineStatus?
    @Published private(set) var buildStatus: PipelineStatus?
    @Published private(set) var jobs: [JobResponse] = []
    @Published private(set) var imports: [ImportFile] = []
    @Published private(set) var triage: [TriageRow] = []
    @Published private(set) var runRecord: RunRecord?
    @Published private(set) var groupingPlan: ClusterPlan?
    @Published private(set) var buildActions: [BuildLogEntry] = []
    @Published private(set) var compilationProvider = ""
    @Published private(set) var compilationModel = ""
    @Published private(set) var buildModels = BuildModels.loading
    @Published var inferenceStatus: InferenceStatusResponse?
    @Published private(set) var connected = false
    @Published private(set) var connectionError: String? {
        didSet { if oldValue != connectionError { connectionErrorDismissed = false } }
    }
    @Published private var connectionErrorDismissed = false
    @Published var actionError: String?
    @Published private var removingSources: Set<String> = []
    @Published private var dismissedRuns: [String: String]
    let api: BackendClient
    private var updates: Task<Void, Never>?
    private var refreshing = false
    private var startingBuild = false
    private var buildModelsProvider = "" // The provider `buildModels` was read for.
    private static let dismissedKey = "dismissedJobOutcomes"

    init(config: AppConfig, api: BackendClient? = nil) {
        self.api = api ?? BackendClient(config: config)
        dismissedRuns = UserDefaults.standard.dictionary(forKey: Self.dismissedKey) as? [String: String] ?? [:]
    }

    deinit { updates?.cancel() }

    func setWindowVisible(_ visible: Bool) {
        if !visible { updates?.cancel(); updates = nil; return }
        guard updates == nil else { return }
        let api = self.api
        updates = Task { [weak self] in
            var retrySeconds: UInt64 = 1
            while !Task.isCancelled {
                do {
                    try await api.receiveSnapshots { [weak self] snapshot in
                        self?.apply(snapshot)
                        retrySeconds = 1
                    }
                } catch {
                    guard !Task.isCancelled else { return }
                    self?.connected = false
                    self?.inferenceStatus = nil
                    self?.connectionError = error.localizedDescription
                }
                do { try await Task.sleep(nanoseconds: retrySeconds * 1_000_000_000) }
                catch { return }
                retrySeconds = min(retrySeconds * 2, 15)
            }
        }
    }

    var status: PipelineStatus? { buildStatus ?? ingestStatus }
    var isActive: Bool { ingestStatus != nil || buildStatus != nil }
    var isCompiling: Bool { buildStatus?.phase == "compile" }
    var isGrouping: Bool { buildStatus?.phase == "cluster" }
    var locked: Bool { buildStatus != nil }
    var stagedForBuild: [StagedSource] { (state?.staged ?? []).filter { $0.deferReason == nil && !isRemoving($0.id) } }
    var setAside: [StagedSource] { (state?.staged ?? []).filter { $0.deferReason != nil && !isRemoving($0.id) } }
    var pendingTriage: [String] { state?.pendingTriage ?? [] }
    var builtCount: Int { state?.builtCount ?? 0 }
    var needsReviewCount: Int { state?.needsReviewCount ?? 0 }
    var skippedCount: Int { state?.skippedCount ?? 0 }
    var stateStamp: String { state?.generatedAt ?? "" }
    var stale: Bool { state?.stale ?? false }
    var hasState: Bool { state != nil }
    var buildingSources: Set<String> { Set(buildProgress?.currentSources ?? []) }
    var buildProgress: PipelineStatus? { isCompiling ? buildStatus : nil }
    var triageProgress: PipelineStatus? { ingestStatus?.phase == "triage" ? ingestStatus : nil }
    var estimatedCostUSD: Double? { state?.estimatedCostUSD }
    /// Retries of the same failure stay silent until the error changes or the connection recovers.
    var connectionNotice: String? { connectionErrorDismissed ? nil : connectionError }
    func dismissConnectionError() { connectionErrorDismissed = true }
    /// A saved model the connected provider still offers. Assumed usable until the first read lands.
    var hasBuildModel: Bool {
        switch buildModels {
        case .loading: return !compilationModel.isEmpty
        case .available(let models): return models.contains { $0.id == compilationModel }
        case .disconnected, .failed: return false
        }
    }
    func ingestFailureReason(for path: String) -> String? { state?.ingestFailures[path] }
    func isRemoving(_ path: String) -> Bool { removingSources.contains(path) }

    func refresh() { Task { await refreshNow() } }

    func refreshNow() async {
        guard !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        do {
            apply(try await api.request("/desktop-state"))
        } catch {
            connected = false
            inferenceStatus = nil
            connectionError = error.localizedDescription
        }
    }

    private func apply(_ snapshot: DesktopSnapshot) {
        func active(_ lane: String) -> PipelineStatus? {
            let pending = snapshot.jobs.filter { $0.job.lane == lane && $0.job.isActive }
            return (pending.first { $0.job.status == "running" } ?? pending.last).map(PipelineStatus.init)
        }
        jobs = snapshot.jobs.map(\.job)
        imports = snapshot.imports
        triage = snapshot.triage.filter(\.available).compactMap(\.row)
        ingestStatus = active("ingest")
        buildStatus = active("build")
        groupingPlan = snapshot.grouping_plan
        buildActions = snapshot.build_actions.compactMap(\.entry)
        compilationProvider = snapshot.compilation_provider
        // With no provider chosen there are no models to read, only Settings to open.
        if compilationProvider.isEmpty { buildModels = .disconnected }
        compilationModel = snapshot.compilation_model
        inferenceStatus = snapshot.inference
        runRecord = RunRecord(
            ingest: jobs.first { $0.lane == "ingest" && !$0.isActive }?.stageRun,
            compile: jobs.first { $0.lane == "build" && !$0.isActive && $0.kind != "index" }?.stageRun)
        state = snapshot.state
        connected = true
        connectionError = nil
    }

    func perform(_ operation: @escaping () async throws -> Void) {
        actionError = nil
        Task {
            do { try await operation(); await refreshNow() }
            catch { actionError = error.localizedDescription }
        }
    }

    func requestBuild() {
        guard !startingBuild else { return }
        startingBuild = true
        actionError = nil
        Task {
            defer { startingBuild = false }
            do { try await api.send("/builds", method: "POST", body: [:]); await refreshNow() }
            catch { actionError = error.localizedDescription }
        }
    }
    /// Settings can connect or disconnect the provider. Don't show the old answer while rereading.
    func forgetBuildModels() { buildModels = .loading }

    /// Reread what the saved provider offers. The last answer stays on screen meanwhile.
    func refreshBuildModels() async {
        let provider = compilationProvider
        guard !provider.isEmpty else {
            // Before the first snapshot there is nothing to say. After it, no provider is chosen.
            if hasState { buildModels = .disconnected }
            return
        }
        if provider != buildModelsProvider {
            buildModelsProvider = provider
            buildModels = .loading
        }
        struct Connection: Decodable { let connected: Bool }
        let read: BuildModels
        do {
            let models: [ModelChoiceResponse] = try await api.request("/providers/\(provider)/models")
            // A missing login and a connected account without models both list nothing.
            let connection: Connection? = models.isEmpty ? try await api.request("/providers/\(provider)/connection") : nil
            read = connection?.connected == false ? .disconnected : .available(models)
        } catch {
            guard !Task.isCancelled else { return }
            read = .failed(error.localizedDescription)
        }
        if provider == compilationProvider { buildModels = read }
    }

    func selectBuildModel(_ id: String) {
        let previous = compilationModel
        guard id != previous else { return }
        compilationModel = id // Show the choice at once; the next snapshot confirms it.
        actionError = nil
        Task {
            do {
                try await api.send("/settings", method: "PATCH", body: ["compilation": ["model": id]])
                await refreshNow()
            } catch {
                compilationModel = previous
                actionError = error.localizedDescription
            }
        }
    }

    func previewGrouping() { perform { try await self.api.send("/grouping-plan/preview", method: "POST") } }
    func requestStop(jobID: String) {
        perform { try await self.api.send("/jobs/\(jobID)/cancel", method: "POST") }
    }
    func retryIngestion(_ path: String) {
        perform { try await self.api.send("/ingestion/runs", method: "POST", body: ["file_path": path]) }
    }
    func setTriage(_ path: String, decision: String) {
        perform { try await self.api.send("/triage", method: "PATCH", body: ["raw_paths": [path], "decision": decision]) }
    }
    func recompile(_ path: String) {
        perform { try await self.api.send("/sources/recompile", method: "POST", body: ["raw_paths": [path]]) }
    }
    func removeStagedSource(_ path: String) { remove(path, endpoint: "/sources", parameter: "raw_path") }
    func removeDroppedSource(_ path: String) { remove(path, endpoint: "/imports", parameter: "file_path") }
    private func remove(_ path: String, endpoint: String, parameter: String) {
        guard !isRemoving(path) else { return }
        removingSources.insert(path)
        actionError = nil
        Task {
            defer { removingSources.remove(path) }
            do { try await api.send(endpoint, method: "DELETE", query: [parameter: path]); await refreshNow() }
            catch { actionError = error.localizedDescription }
        }
    }
    func notice(for stage: PipelineStage) -> RunNotice? {
        guard !(stage == .ingest ? ingestStatus != nil : buildStatus != nil),
              let run = runRecord?.run(for: stage), run.needsAttention,
              dismissedRuns[stage.rawValue] != run.finishedAt else { return nil }
        return RunNotice(stage: stage, run: run)
    }
    func dismiss(_ notice: RunNotice) {
        dismissedRuns[notice.stage.rawValue] = notice.run.finishedAt
        UserDefaults.standard.set(dismissedRuns, forKey: Self.dismissedKey)
    }
}
