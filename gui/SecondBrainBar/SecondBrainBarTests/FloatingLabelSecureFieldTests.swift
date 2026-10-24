import AppKit
import SwiftUI
import XCTest
@testable import SecondBrainBar

@MainActor
final class FloatingLabelSecureFieldTests: XCTestCase {
    private final class Draft: ObservableObject {
        @Published var key = ""
    }

    private struct FieldPreview: View {
        @ObservedObject var draft: Draft

        var body: some View {
            FloatingLabelSecureField(title: "Anthropic API key", text: $draft.key)
                .frame(width: 280)
                .transaction { $0.disablesAnimations = true }
        }
    }

    private func settle(_ view: NSView) {
        RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        view.layoutSubtreeIfNeeded()
    }

    private func secureField(in view: NSView) -> NSSecureTextField? {
        if let field = view as? NSSecureTextField { return field }
        return view.subviews.lazy.compactMap { self.secureField(in: $0) }.first
    }

    func testFieldUsesSecureEntryWithoutASecondPlaceholder() throws {
        let draft = Draft()
        let hosting = NSHostingView(rootView: FieldPreview(draft: draft))
        hosting.frame = NSRect(x: 0, y: 0, width: 280, height: 52)
        let window = NSWindow(contentRect: hosting.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = hosting
        defer { window.contentView = nil }
        settle(hosting)

        let field = try XCTUnwrap(secureField(in: hosting))
        XCTAssertEqual(field.placeholderString ?? "", "")
        XCTAssertEqual(field.stringValue, "")
        XCTAssertEqual(hosting.fittingSize.height, 52, accuracy: 0.5)
    }

    func testFocusTypingAndClearingKeepTheFieldHeightAndBinding() throws {
        let draft = Draft()
        let hosting = NSHostingView(rootView: FieldPreview(draft: draft))
        hosting.frame = NSRect(x: 0, y: 0, width: 280, height: 52)
        let window = NSWindow(contentRect: hosting.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = hosting
        defer { window.contentView = nil }
        settle(hosting)

        let field = try XCTUnwrap(secureField(in: hosting))
        XCTAssertTrue(window.makeFirstResponder(field))
        settle(hosting)
        let editor = try XCTUnwrap(field.currentEditor() as? NSTextView)
        editor.insertText("test-only-key", replacementRange: NSRange(location: NSNotFound, length: 0))
        settle(hosting)
        XCTAssertEqual(draft.key, "test-only-key")
        XCTAssertEqual(hosting.fittingSize.height, 52, accuracy: 0.5)

        editor.selectAll(nil)
        editor.insertText("replacement", replacementRange: NSRange(location: NSNotFound, length: 0))
        settle(hosting)
        XCTAssertEqual(draft.key, "replacement")

        XCTAssertTrue(window.makeFirstResponder(nil))
        settle(hosting)
        XCTAssertEqual(field.stringValue, "replacement")
        XCTAssertEqual(hosting.fittingSize.height, 52, accuracy: 0.5)

        draft.key = ""
        settle(hosting)
        XCTAssertEqual(field.stringValue, "")
        XCTAssertEqual(hosting.fittingSize.height, 52, accuracy: 0.5)
    }
}
