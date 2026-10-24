import XCTest
@testable import SecondBrainBar

final class PipelineStatusTests: XCTestCase {
    private func detail(kind: String = "build", status: String = "running", result: [String: Any] = [:]) throws -> JobDetail {
        let data = try JSONSerialization.data(withJSONObject: [
            "job": APIFixtures.job(kind: kind, status: status, result: result),
            "progress": ["current": 0, "total": 2],
        ])
        return try JSONDecoder().decode(JobDetail.self, from: data)
    }

    func testElapsedTimeAdvancesWithoutAnotherBackendSnapshot() throws {
        let status = PipelineStatus(try detail())
        XCTAssertEqual(status.footerDetail(at: status.startedAt.addingTimeInterval(65)), "Building 1/2 · 1m 5s")
        XCTAssertEqual(status.footerDetail(at: status.startedAt.addingTimeInterval(66)), "Building 1/2 · 1m 6s")
    }

    func testQueuedJobsDoNotPretendToBeRunningAndAllRunningLanesShowTime() throws {
        let queued = PipelineStatus(try detail(status: "queued"))
        XCTAssertEqual(queued.footerDetail(at: queued.startedAt.addingTimeInterval(60)), "Build queued")
        for (kind, verb) in [("ingest", "Ingesting"), ("preview", "Grouping"), ("index", "Indexing")] {
            let active = PipelineStatus(try detail(kind: kind))
            XCTAssertEqual(active.footerDetail(at: active.startedAt.addingTimeInterval(7)), "\(verb) 1/2 · 7s")
        }
    }

    func testTerminalStatusOverridesAnEarlierSuccessfulResult() throws {
        for state in ["failed", "interrupted"] {
            let job = try detail(status: state, result: ["outcome": "ok"]).job
            XCTAssertEqual(job.stageRun.outcome, .failed)
            XCTAssertTrue(job.stageRun.needsAttention)
        }
        XCTAssertEqual(try detail(status: "cancelled", result: ["outcome": "ok"]).job.stageRun.outcome, .stopped)
        XCTAssertEqual(try detail(status: "succeeded", result: ["outcome": "capped"]).job.stageRun.outcome, .capped)
    }
}
