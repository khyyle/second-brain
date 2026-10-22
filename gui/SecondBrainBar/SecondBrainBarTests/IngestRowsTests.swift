import XCTest
@testable import SecondBrainBar

final class IngestRowsTests: XCTestCase {
    func testDuplicateRemainsVisibleAfterItsDropCopyDisappears() {
        var rows = IngestRows()
        rows.add([QueueItem(
            id: "/drops/conversations.json", displayName: "conversations.json",
            state: .duplicate, since: Date.distantPast
        )])

        rows.update(live: [], failedIDs: []) { _ in nil }

        XCTAssertEqual(rows.items.count, 1)
        XCTAssertEqual(rows.items.first?.state, .duplicate)
        XCTAssertEqual(rows.items.first?.state.label, "already processed")
    }

    func testMissingHeartbeatKeepsChatsInTheirOriginalPositions() {
        var rows = IngestRows()
        let chats = ["chatgpt/a.md", "chatgpt/b.md"].map { path in
            QueueItem(id: path, displayName: path, state: .waiting, since: Date())
        }
        rows.add(chats)

        rows.update(live: [], failedIDs: []) { _ in nil }
        XCTAssertEqual(rows.items.map(\.id), chats.map(\.id))

        rows.update(live: [chats[1]], failedIDs: []) { _ in .kept }
        XCTAssertEqual(rows.items.map(\.id), chats.map(\.id))
        XCTAssertEqual(rows.items.first?.outcome, .kept)
        XCTAssertEqual(rows.items.last?.state, .waiting)
    }

    func testResubmittingAFileReplacesItsPreviousCompletion() {
        var rows = IngestRows()
        rows.add([QueueItem(
            id: "/drops/notes.md", displayName: "notes.md", state: .duplicate, since: Date()
        )])

        rows.add([QueueItem(
            id: "/drops/notes.md", displayName: "notes.md", state: .waiting, since: Date()
        )])

        XCTAssertEqual(rows.items.count, 1)
        XCTAssertEqual(rows.items.first?.state, .waiting)
    }

    func testFailedFileLeavesProgressWithoutShowingSuccess() {
        var rows = IngestRows()
        let submitted = QueueItem(
            id: "/drops/notes.md", displayName: "notes.md", state: .waiting, since: Date()
        )
        rows.add([submitted])

        rows.update(live: [], failedIDs: [submitted.id]) { _ in .staged }

        XCTAssertTrue(rows.items.isEmpty)
    }
}
