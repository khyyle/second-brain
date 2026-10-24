import Darwin
import Foundation
import XCTest
@testable import SecondBrainBar

@MainActor
final class BackendProcessTests: XCTestCase {
    func testCancelledStartupStopsItsChild() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let script = directory.appending(path: "backend.sh")
        try "echo $$ > \"$SECOND_BRAIN_VAULT/child.pid\"\nexec /bin/sleep 30\n"
            .write(to: script, atomically: true, encoding: .utf8)
        let backend = BackendProcess()
        defer { backend.stop() }
        let task = Task {
            try await backend.connectOrStart(config: AppConfig(vaultRoot: directory, backendScript: script))
        }
        defer { task.cancel() }

        let pidFile = directory.appending(path: "child.pid")
        let deadline = Date().addingTimeInterval(5)
        while !FileManager.default.fileExists(atPath: pidFile.path), Date() < deadline {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        let pid = try XCTUnwrap(Int32(try String(contentsOf: pidFile, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)))
        XCTAssertEqual(kill(pid, 0), 0)

        task.cancel()
        do {
            try await task.value
            XCTFail("Cancelled startup must not report a ready backend.")
        } catch is CancellationError {
        }
        let stopDeadline = Date().addingTimeInterval(5)
        while kill(pid, 0) == 0, Date() < stopDeadline {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTAssertEqual(kill(pid, 0), -1, "Startup cancellation left its child running.")
        XCTAssertEqual(errno, ESRCH)
    }
}
