import XCTest
@testable import SecondBrainBar

final class PipelineStoreTests: XCTestCase {
    @MainActor
    func testRemovalHidesTheSourceImmediatelyAndRestoresItIfDeletionFails() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        let config = AppConfig(vaultRoot: directory, runScriptPath: nil)
        let source = config.rawRoot.appending(path: "documents/notes.md")
        try FileManager.default.createDirectory(at: source.deletingLastPathComponent(), withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        try Data("notes".utf8).write(to: source)
        let snapshot: [String: Any] = [
            "staged": [["rel": "documents/notes.md"]],
            "built_count": 0, "costs": [:], "stale": false
        ]
        try JSONSerialization.data(withJSONObject: snapshot).write(to: config.stateFile)
        let store = PipelineStore(config: config)
        XCTAssertEqual(store.stagedForBuild.count, 1)
        let failed = expectation(description: "The missing pipeline location is reported")
        let observation = store.$removalError.compactMap { $0 }.sink { _ in failed.fulfill() }
        defer { observation.cancel() }

        store.removeStagedSource("documents/notes.md")

        XCTAssertTrue(store.stagedForBuild.isEmpty)
        XCTAssertTrue(store.isRemoving("documents/notes.md"))
        await fulfillment(of: [failed], timeout: 2)
        XCTAssertEqual(store.stagedForBuild.count, 1)
        XCTAssertFalse(store.isRemoving("documents/notes.md"))
        XCTAssertTrue(FileManager.default.fileExists(atPath: source.path))
    }
}
