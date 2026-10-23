import SQLite3
import XCTest
@testable import SecondBrainBar

final class IngestQueueTests: XCTestCase {
    private var directory: URL!
    private var config: AppConfig!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        config = AppConfig(vaultRoot: directory, runScriptPath: nil)
    }

    override func tearDownWithError() throws {
        try FileManager.default.removeItem(at: directory)
    }

    func testWatchedFolderFailuresAppearWithoutADropFolder() throws {
        let source = directory.appending(path: "watched/damaged.pdf")
        try record(source, status: "failed")

        let queue = IngestQueue.queue(config: config)

        XCTAssertEqual(queue.map(\.id), [source.path])
        XCTAssertEqual(queue.map(\.state), [.failed])
        XCTAssertEqual(queue.map(\.displayName), ["damaged.pdf"])

        try record(source, status: "complete")
        XCTAssertTrue(IngestQueue.queue(config: config).isEmpty)
    }

    func testDroppedFailuresAreListedOnceAlongsideWatchedFolderFailures() throws {
        let dropped = config.dropsRoot.appending(path: "documents/damaged.pdf")
        try FileManager.default.createDirectory(
            at: dropped.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try Data("damaged".utf8).write(to: dropped)
        let watched = directory.appending(path: "watched/damaged.pdf")
        try record(dropped, status: "failed")
        try record(watched, status: "failed")

        let queue = IngestQueue.queue(config: config)

        XCTAssertEqual(queue.count, 2)
        XCTAssertEqual(Set(queue.map(\.id)), [dropped.path, watched.path])
        XCTAssertTrue(queue.allSatisfy { $0.state == .failed })
    }

    func testAnOldFailureDoesNotOverrideAResubmittedFile() throws {
        let source = config.dropsRoot.appending(path: "documents/damaged.pdf")
        try FileManager.default.createDirectory(
            at: source.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try Data("repaired".utf8).write(to: source)
        try record(source, status: "failed")
        let submitted = QueueItem(
            id: source.path, displayName: "damaged.pdf", state: .waiting, since: Date()
        )

        let queue = IngestQueue.queue(config: config, submitted: [submitted])

        XCTAssertEqual(queue.map(\.state), [.waiting])
    }

    private func record(_ source: URL, status: String) throws {
        var database: OpaquePointer?
        XCTAssertEqual(sqlite3_open(config.manifestDB.path, &database), SQLITE_OK)
        defer { sqlite3_close(database) }
        let sql = """
            CREATE TABLE IF NOT EXISTS manifest (
                file_path TEXT PRIMARY KEY, status TEXT, updated_at TEXT
            );
            INSERT OR REPLACE INTO manifest VALUES (
                '\(source.path)', '\(status)', '2026-01-01T00:00:00.000Z'
            );
        """
        XCTAssertEqual(sqlite3_exec(database, sql, nil, nil, nil), SQLITE_OK)
    }
}
