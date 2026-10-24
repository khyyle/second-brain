import XCTest
@testable import SecondBrainBar

final class PipelineStoreTests: XCTestCase {
    @MainActor
    func testBuildRejectionShowsRequestReasonWithoutInventingAFailedJobAndClearsOnRetry() async throws {
        var reject = true
        let reason = "No anthropic credential is available in Keychain. Add it in Settings, then build again."
        let harness = try APITestHarness { request -> (Int, Any) in
            if request.url?.path == "/v1/builds" {
                if reject { return (409, ["detail": reason]) }
                return (202, APIFixtures.job(status: "queued"))
            }
            return (200, APIFixtures.desktop())
        }
        let store = PipelineStore(config: harness.config, api: harness.client)
        let rejected = expectation(description: "Build request rejected")
        let errorObservation = store.$actionError.compactMap { $0 }.sink { _ in rejected.fulfill() }
        store.requestBuild()
        await fulfillment(of: [rejected], timeout: 2)
        errorObservation.cancel()
        XCTAssertEqual(store.actionError, reason)
        XCTAssertNil(store.runRecord)
        XCTAssertTrue(store.jobs.isEmpty)

        reject = false
        let refreshed = expectation(description: "Accepted build refreshes state")
        let stateObservation = store.$connected.filter { $0 }.sink { _ in refreshed.fulfill() }
        defer { stateObservation.cancel() }
        store.requestBuild()
        XCTAssertNil(store.actionError)
        await fulfillment(of: [refreshed], timeout: 2)
        XCTAssertNil(store.actionError)
    }

    @MainActor
    func testTerminalSnapshotRemovesTheLiveTimerAndRetainsTheFailure() async throws {
        var running = true
        let harness = try APITestHarness { _ in
            (200, APIFixtures.desktop(jobs: [[
                "job": APIFixtures.job(status: running ? "running" : "failed", error: running ? "" : "Provider unavailable"),
                "progress": ["phase": "compile"],
            ]]))
        }
        let store = PipelineStore(config: harness.config, api: harness.client)
        await store.refreshNow()
        XCTAssertTrue(store.isActive)
        running = false
        await store.refreshNow()
        XCTAssertFalse(store.isActive)
        XCTAssertNil(store.buildStatus)
        XCTAssertEqual(store.runRecord?.compile?.reason, "Provider unavailable")
    }

    @MainActor
    func testIngestAndBuildProgressRemainIndependent() async throws {
        let harness = try APITestHarness { request in
            XCTAssertEqual(request.url!.path, "/v1/desktop-state")
            return (200, APIFixtures.desktop(jobs: [
                ["job": APIFixtures.job(id: "ingest-1", kind: "ingest"),
                 "progress": ["phase": "triage", "current_sources": ["chatgpt/a.md"]]],
                ["job": APIFixtures.job(),
                 "progress": ["phase": "compile", "current_sources": ["documents/b.md"]]],
            ]))
        }
        let store = PipelineStore(config: harness.config, api: harness.client)
        await store.refreshNow()
        XCTAssertTrue(store.connected)
        XCTAssertEqual(store.triageProgress?.currentSources, ["chatgpt/a.md"])
        XCTAssertEqual(store.buildProgress?.currentSources, ["documents/b.md"])
    }

    @MainActor
    func testFailedRemovalRestoresSourceAndShowsBackendReason() async throws {
        let harness = try APITestHarness { request in
            if request.httpMethod == "DELETE" { return (409, ["detail": "A build is using this source."]) }
            XCTAssertEqual(request.url!.path, "/v1/desktop-state")
            return (200, APIFixtures.desktop(staged: ["documents/a.md"]))
        }
        let store = PipelineStore(config: harness.config, api: harness.client)
        await store.refreshNow()
        let failed = expectation(description: "API error surfaced")
        let observation = store.$actionError.compactMap { $0 }.sink { _ in failed.fulfill() }
        defer { observation.cancel() }
        store.removeStagedSource("documents/a.md")
        XCTAssertTrue(store.stagedForBuild.isEmpty)
        await fulfillment(of: [failed], timeout: 2)
        XCTAssertEqual(store.stagedForBuild.count, 1)
        XCTAssertEqual(store.actionError, "A build is using this source.")
    }
}
