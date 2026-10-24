import XCTest
@testable import SecondBrainBar

final class IngestQueueTests: XCTestCase {
    func testQueueUsesBackendOutcomesWithoutReadingVaultFiles() throws {
        let data = try JSONSerialization.data(withJSONObject: [
            APIFixtures.importFile(path: "/missing/pending.md"),
            APIFixtures.importFile(path: "/missing/failed.md", status: "failed"),
            APIFixtures.importFile(status: "complete"),
            APIFixtures.importFile(status: "duplicate")
        ])
        let records = try JSONDecoder().decode([ImportFile].self, from: data)
        let rows = IngestQueue.queue(imports: records)
        XCTAssertEqual(rows.map(\.id), ["/missing/pending.md", "/missing/failed.md"])
        XCTAssertEqual(rows.map(\.state), [.waiting, .failed])
    }
}
