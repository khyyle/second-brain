import SwiftUI
import XCTest
@testable import SecondBrainBar

@MainActor
final class ProviderLoginViewTests: XCTestCase {
    func testProviderSpecificSecretUsesSecureEntry() async throws {
        try await withPrompt(method: "api_key", events: [
            ["type": "prompt", "id": 1, "kind": "secret", "message": "Example API key"]
        ]) { XCTAssertNotNil(secureField(in: $0)) }
    }

    func testOnlyBrowserCallbacksCollapseManualEntry() async throws {
        let loopback = "http://127.0.0.1:1455/auth/callback"
        for (callback, listening, expectsField) in [(loopback, true, false), (loopback, false, true),
                                                    ("https://example.test/show-code", false, true)] {
            try await withPrompt(method: "oauth", events: [
                ["type": "auth_url", "url": "https://example.test/login?redirect_uri=\(callback)",
                 "instructions": "Complete sign-in in your browser."],
                ["type": "prompt", "id": 1, "kind": "manual_code", "callback_listening": listening,
                 "message": "Paste your code or redirect URL."]
            ]) { view in
                XCTAssertEqual(secureField(in: view) != nil, expectsField)
            }
        }
    }

    private func withPrompt(method: String, events: [[String: Any]], check: (NSView) throws -> Void) async throws {
        let waiting = expectation(description: "Polling after the prompt")
        let release = DispatchSemaphore(value: 0)
        var events = events
        let harness = try APITestHarness { request in
            switch (request.httpMethod, request.url?.path) {
            case ("POST", "/v1/providers/example/logins"): return (200, ["login_id": "session"])
            case ("POST", "/v1/logins/session/next-event"):
                if !events.isEmpty { return (200, events.removeFirst()) }
                waiting.fulfill()
                _ = release.wait(timeout: .now() + 5)
                return (200, ["type": "cancelled"])
            case ("DELETE", "/v1/logins/session"): return (204, NSNull())
            default: XCTFail("Unexpected sign-in request"); return (404, [:])
            }
        }
        let form = ProviderLoginView(api: harness.client, provider: "example", method: method, onFinish: {})
        let host = NSHostingView(rootView: form.frame(width: 280).padding(12).background(Theme.Colors.background).preferredColorScheme(.dark))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 306, height: 128), styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = host
        defer { release.signal(); window.contentView = nil }
        host.layoutSubtreeIfNeeded()
        await fulfillment(of: [waiting], timeout: 2)
        host.layoutSubtreeIfNeeded()
        try check(host)
    }

    private func secureField(in view: NSView) -> NSSecureTextField? {
        if let field = view as? NSSecureTextField { return field }
        return view.subviews.lazy.compactMap { self.secureField(in: $0) }.first
    }
}
