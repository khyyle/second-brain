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
    @Published private(set) var compilationModel = ""
    @Published private(set) var connected = false
    @Published private(set) var connectionError: String?
    @Published var actionError: String?
    @Published private var removingSources: Set<String> = []
    @Published private var dismissedRuns: [String: String]
    let api: BackendClient
    private var updates: Task<Void, Never>?
    private var refreshing = false
    private var startingBuild = false
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
    var stopping: Bool { jobs.contains { $0.lane == "build" && $0.isActive && $0.cancel_requested } }
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
    func cost(for model: String) -> Double { state?.costs[model] ?? 0 }
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
        compilationModel = snapshot.compilation_model
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
    func previewGrouping() { perform { try await self.api.send("/grouping-plan/preview", method: "POST") } }
    func requestStop() {
        guard let job = jobs.first(where: { $0.lane == "build" && $0.status == "running" })
                ?? jobs.last(where: { $0.lane == "build" && $0.isActive }) else { return }
        perform { try await self.api.send("/jobs/\(job.id)/cancel", method: "POST") }
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
