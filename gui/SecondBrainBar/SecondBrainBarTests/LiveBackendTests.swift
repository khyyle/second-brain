import XCTest
@testable import SecondBrainBar

/// Opt-in contract test against an isolated backend started by make gui-smoke.
final class LiveBackendTests: XCTestCase {
    @MainActor
    func testServerEventsUpdateAndResynchronizeAfterReopening() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let vault = env["SECOND_BRAIN_LIVE_TEST_VAULT"],
              let port = env["SECOND_BRAIN_LIVE_TEST_PORT"].flatMap(Int.init) else {
            throw XCTSkip("Run make gui-smoke to test server events.")
        }
        let config = AppConfig(vaultRoot: URL(fileURLWithPath: vault), port: port)
        let store = PipelineStore(config: config)
        store.setWindowVisible(true)
        defer { store.setWindowVisible(false) }
        let initial = expectation(description: "Initial snapshot arrived without polling")
        let subscription = store.$state.compactMap { $0 }.first().sink { _ in initial.fulfill() }
        defer { subscription.cancel() }
        await fulfillment(of: [initial], timeout: 10)
        XCTAssertTrue(store.connected, store.connectionError ?? "")

        let added = expectation(description: "External vault write pushed to Swift")
        let updates = store.$state.compactMap { $0 }
            .filter { $0.staged.contains { $0.id == "documents/sse-notes.md" } }
            .first().sink { _ in added.fulfill() }
        defer { updates.cancel() }
        let document = config.rawRoot.appending(path: "documents/sse-notes.md")
        try FileManager.default.createDirectory(at: document.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("# Stream test\n\nA file changed outside the HTTP process.".utf8).write(to: document)
        await fulfillment(of: [added], timeout: 10)

        store.setWindowVisible(false)
        try FileManager.default.removeItem(at: document)
        let reopened = expectation(description: "Reconnect receives fresh state after missed change")
        let resumed = store.$state.compactMap { $0 }
            .filter { !$0.staged.contains { $0.id == "documents/sse-notes.md" } }
            .first().sink { _ in reopened.fulfill() }
        defer { resumed.cancel() }
        store.setWindowVisible(true)
        await fulfillment(of: [reopened], timeout: 10)
    }

    @MainActor
    func testImportBuildSettingsAndDeletionOverRealHTTP() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let vault = env["SECOND_BRAIN_LIVE_TEST_VAULT"],
              let port = env["SECOND_BRAIN_LIVE_TEST_PORT"].flatMap(Int.init) else {
            throw XCTSkip("Run make gui-smoke to test the real local backend.")
        }
        let config = AppConfig(vaultRoot: URL(fileURLWithPath: vault), port: port)
        let api = BackendClient(config: config)
        let _: PreferencesResponse = try await api.request("/settings")
        let catalog: ApplicationCatalog = try await api.request("/catalog")
        XCTAssertFalse(catalog.providers.isEmpty)
        try await api.send("/settings", method: "PATCH", body: [
            "triage": ["enabled": false], "search": ["semantic_enabled": false],
            "compilation": ["max_cost_per_build_usd": 1.25]
        ])
        let original = config.vaultRoot.deletingLastPathComponent().appending(path: "http-smoke-notes.md")
        try Data("# HTTP smoke test\n\nAn isolated document imported by the real Swift client.\n".utf8).write(to: original)
        defer { try? FileManager.default.removeItem(at: original) }
        let receipt: ImportSubmission = try await api.request("/imports", method: "POST",
            body: ["paths": [original.path], "source_type": "auto"])
        XCTAssertEqual(receipt.files.count, 1)
        try await waitForSuccess(receipt.job, api: api)
        let store = PipelineStore(config: config, api: api)
        await store.refreshNow()
        XCTAssertTrue(store.connected, store.connectionError ?? "")
        XCTAssertEqual(store.stagedForBuild.count, 1)
        let rawPath = try XCTUnwrap(store.stagedForBuild.first?.id)
        let dryRun: JobResponse = try await api.request("/builds", method: "POST", body: ["dry_run": true])
        try await waitForSuccess(dryRun, api: api)
        let _: [BuildActionResponse] = try await api.request("/builds/actions")
        let _: ClusterPlan? = try await api.request("/grouping-plan")
        let _: OllamaHealth = try await api.request("/diagnostics")
        let _: [DomainInfo] = try await api.request("/domains")
        let _: WikiHealthResponse = try await api.request("/wiki/health")
        let settings: PreferencesResponse = try await api.request("/settings")
        XCTAssertEqual(settings.compilation.max_cost_per_build_usd, 1.25)
        try await api.send("/sources", method: "DELETE", query: ["raw_path": rawPath])
        await store.refreshNow()
        XCTAssertTrue(store.stagedForBuild.isEmpty)
        XCTAssertTrue(FileManager.default.fileExists(atPath: original.path))
    }

    private func waitForSuccess(_ job: JobResponse, api: BackendClient) async throws {
        for _ in 0..<150 {
            let detail: JobDetail = try await api.request("/jobs/\(job.id)")
            if !detail.job.isActive {
                XCTAssertEqual(detail.job.status, "succeeded", detail.job.error)
                return
            }
            try await Task.sleep(nanoseconds: 200_000_000)
        }
        XCTFail("Job did not finish within 30 seconds")
    }
}
