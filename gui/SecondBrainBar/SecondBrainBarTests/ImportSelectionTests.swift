import XCTest
@testable import SecondBrainBar

final class ImportSelectionTests: XCTestCase {
    func testFolderSelectionKeepsSupportedDocumentsAndDoesNotCopyAnything() throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: directory.appending(path: "nested"), withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        for path in ["a.md", "nested/b.tex", "attachment.png", ".hidden.txt"] {
            try Data("notes".utf8).write(to: directory.appending(path: path))
        }
        XCTAssertEqual(Set(ImportSelection.documentFiles(in: [directory]).map(\.lastPathComponent)), ["a.md", "b.tex"])
    }
}
