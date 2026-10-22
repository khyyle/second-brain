import CryptoKit
import SQLite3
import XCTest
@testable import SecondBrainBar

final class DropStagingTests: XCTestCase {
    private var directory: URL!
    private var config: AppConfig!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        config = AppConfig(vaultRoot: directory.appending(path: "vault"), runScriptPath: nil)
        try FileManager.default.createDirectory(at: config.vaultRoot, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try FileManager.default.removeItem(at: directory)
    }

    func testDuplicateReceiptSurvivesRemovalOfItsTemporaryCopy() throws {
        let export = directory.appending(path: "conversations.json")
        try Data("[{\"mapping\":{}}]".utf8).write(to: export)
        try recordComplete(export, rawOutput: "chatgpt/chat.md")
        XCTAssertTrue(DropStaging.isAlreadyIngested([export], config: config))
        let held = config.rawRoot.appending(path: ".skipped/chatgpt/chat.md")
        try FileManager.default.createDirectory(at: held.deletingLastPathComponent(), withIntermediateDirectories: true)
        try FileManager.default.moveItem(at: config.rawRoot.appending(path: "chatgpt/chat.md"), to: held)

        let staged = DropStaging.importExport(.chatgpt, files: [export], config: config)
        let copied = try XCTUnwrap(staged.files.first)
        try FileManager.default.removeItem(atPath: copied.id)

        XCTAssertTrue(DropStaging.isAlreadyIngested([export], config: config))
        XCTAssertEqual(staged.files.map(\.state), [.duplicate])
        var rows = IngestRows()
        rows.add(staged.files)
        rows.update(live: IngestQueue.queue(config: config), failedIDs: []) { _ in nil }
        XCTAssertEqual(rows.items.map(\.state), [.duplicate])
    }

    func testMixedFolderKeepsDocumentsAlongsideItsExport() throws {
        let export = directory.appending(path: "conversations.json")
        try Data("[{\"mapping\":{}}]".utf8).write(to: export)
        try Data("# Notes".utf8).write(to: directory.appending(path: "notes.md"))
        let detected = try XCTUnwrap(ExportProvider.detect(in: [directory]))

        XCTAssertTrue(detected.exportFolders.isEmpty)
        let documents = DropStaging.stageDocuments([directory], config: config)
        XCTAssertEqual(documents.files.map(\.displayName), ["notes.md"])

        try Data().write(to: directory.appending(path: "chat.html"))
        XCTAssertEqual(ExportProvider.detect(in: [directory])?.exportFolders, [directory!])
    }

    func testNewCopyDoesNotInheritThePreviousCopiesCompletion() throws {
        let source = directory.appending(path: "notes.md")
        try Data("old text".utf8).write(to: source)
        let previous = DropStaging.stageDocuments([source], config: config)
        let path = try XCTUnwrap(previous.files.first?.id)
        try recordComplete(URL(fileURLWithPath: path), rawOutput: "documents/notes.md")
        try FileManager.default.removeItem(atPath: path)
        try Data("changed text".utf8).write(to: source)

        let submitted = DropStaging.stageDocuments([source], config: config)
        let queue = IngestQueue.queue(config: config, submitted: submitted.files)

        XCTAssertEqual(submitted.files.map(\.state), [.waiting])
        XCTAssertEqual(queue.map(\.state), [.waiting])
    }

    func testSameNamedFilesFromDifferentFoldersAreBothPreserved() throws {
        let first = directory.appending(path: "first/notes.md")
        let second = directory.appending(path: "second/notes.md")
        for source in [first, second] {
            try FileManager.default.createDirectory(
                at: source.deletingLastPathComponent(), withIntermediateDirectories: true
            )
        }
        try Data("first notes".utf8).write(to: first)
        try Data("second notes".utf8).write(to: second)

        let staged = DropStaging.stageDocuments([first, second], config: config)

        XCTAssertEqual(staged.files.map(\.displayName), ["notes.md", "notes-2.md"])
        XCTAssertEqual(
            try staged.files.map { try String(contentsOfFile: $0.id, encoding: .utf8) },
            ["first notes", "second notes"]
        )
    }

    func testDifferentExtensionsCannotShareTheSameParsedOutputName() throws {
        let first = directory.appending(path: "notes.md")
        let second = directory.appending(path: "notes.txt")
        try Data("Markdown notes".utf8).write(to: first)
        try Data("text notes".utf8).write(to: second)

        let staged = DropStaging.stageDocuments([first, second], config: config)

        XCTAssertEqual(staged.files.map(\.displayName), ["notes.md", "notes-2.txt"])
    }

    func testACompletedSourcesOutputIsNotOverwrittenByALaterImport() throws {
        let first = directory.appending(path: "notes.md")
        try Data("first notes".utf8).write(to: first)
        let staged = DropStaging.stageDocuments([first], config: config)
        let originalPath = try XCTUnwrap(staged.files.first?.id)
        try recordComplete(URL(fileURLWithPath: originalPath), rawOutput: "documents/notes.md")
        try FileManager.default.removeItem(atPath: originalPath)
        try Data("second notes".utf8).write(to: first)

        let imported = DropStaging.stageDocuments([first], config: config)

        XCTAssertEqual(imported.files.map(\.displayName), ["notes-2.md"])
        XCTAssertEqual(
            try String(contentsOf: config.rawRoot.appending(path: "documents/notes.md"), encoding: .utf8),
            "parsed text"
        )
    }

    private func recordComplete(_ source: URL, rawOutput: String) throws {
        let raw = config.rawRoot.appending(path: rawOutput)
        try FileManager.default.createDirectory(at: raw.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("parsed text".utf8).write(to: raw)
        let hash = SHA256.hash(data: try Data(contentsOf: source)).map { String(format: "%02x", $0) }.joined()
        var database: OpaquePointer?
        XCTAssertEqual(sqlite3_open(config.manifestDB.path, &database), SQLITE_OK)
        defer { sqlite3_close(database) }
        let timestamp = ISO8601DateFormatter().string(from: Date.distantPast)
        let sql = """
            CREATE TABLE IF NOT EXISTS manifest (
                file_path TEXT, sha256 TEXT, status TEXT, raw_output TEXT, updated_at TEXT
            );
            INSERT INTO manifest VALUES ('\(source.path)', '\(hash)', 'complete', '\(rawOutput)', '\(timestamp)');
        """
        XCTAssertEqual(sqlite3_exec(database, sql, nil, nil, nil), SQLITE_OK)
    }
}
