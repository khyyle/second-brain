import SwiftUI
import XCTest
@testable import SecondBrainBar

@MainActor
final class ProviderLoginSessionTests: XCTestCase {
    func testBrowserCompletesWithAnUnansweredManualPrompt() async throws {
        let waiting = expectation(description: "Polling continues after a prompt and progress")
        let connected = expectation(description: "Browser sign-in completed")
        let finishBrowser = DispatchSemaphore(value: 0)
        var events: [[String: Any]] = [
            ["type": "device_code", "userCode": "TEST-CODE", "verificationUri": "https://example.test/device"],
            ["type": "auth_url", "url": "https://example.test/sign-in", "instructions": "Open the sign-in page."],
            ["type": "info", "message": "Need help?", "links": [["url": "https://example.test/help", "label": "Help"]]],
            ["type": "prompt", "id": 1, "kind": "manual_code", "message": "Paste redirect", "placeholder": "Redirect URL"],
            ["type": "progress", "message": "Waiting for browser"],
        ]
        let harness = try APITestHarness { request in
            XCTAssertEqual(request.httpMethod, "POST")
            switch request.url?.path {
            case "/v1/providers/example/logins":
                XCTAssertEqual(try Self.body(request)["method"] as? String, "oauth")
                return (201, ["login_id": "login-1"])
            case "/v1/logins/login-1/next-event":
                XCTAssertEqual(request.timeoutInterval, 360)
                if !events.isEmpty { return (200, events.removeFirst()) }
                waiting.fulfill()
                XCTAssertEqual(finishBrowser.wait(timeout: .now() + 3), .success)
                return (200, ["type": "done"])
            default: XCTFail("Unexpected sign-in request"); return (404, [:])
            }
        }
        let session = ProviderLoginSession(api: harness.client)
        session.start(provider: "example", method: "oauth") { connected.fulfill() }
        await fulfillment(of: [waiting], timeout: 2)
        XCTAssertEqual(session.display.browserURL?.absoluteString, "https://example.test/sign-in")
        XCTAssertEqual(session.display.instructions, "Open the sign-in page.")
        XCTAssertEqual(session.display.device?.code, "TEST-CODE")
        XCTAssertEqual(session.display.device?.verificationURL.absoluteString, "https://example.test/device")
        XCTAssertEqual(session.display.info, "Need help?")
        XCTAssertEqual(session.display.links.first?.label, "Help")
        XCTAssertEqual(session.display.prompt?.kind, .manualCode)
        XCTAssertEqual(session.display.message, "Waiting for browser")
        finishBrowser.signal()
        await fulfillment(of: [connected], timeout: 2)
        XCTAssertNil(session.display.prompt)
        XCTAssertNil(session.display.device)
    }

    func testMultiplePromptsSendTheSelectedOptionIDAndAllowEmptyText() async throws {
        let connected = expectation(description: "Both answers accepted")
        var accepted = 0
        var lastPrompt = 0
        let harness = try APITestHarness { request in
            switch (request.httpMethod, request.url?.path) {
            case ("POST", "/v1/providers/example/logins"): return (201, ["login_id": "multi"])
            case ("POST", "/v1/logins/multi/next-event"):
                if accepted == 2 { return (200, ["type": "done"]) }
                // Don't block URLProtocol's serial loader: answers use the same test session.
                if lastPrompt == accepted + 1 { return (200, ["type": "progress", "message": "Waiting…"]) }
                lastPrompt += 1
                if lastPrompt == 1 {
                    return (200, ["type": "prompt", "kind": "select", "id": 1, "message": "Choose an account",
                                  "options": [["id": "personal", "label": "My account", "description": "Personal workspace"]]])
                }
                return (200, ["type": "prompt", "kind": "text", "id": 2, "message": "Optional region"])
            case ("POST", "/v1/logins/multi/answers"):
                let body = try Self.body(request)
                accepted += 1
                XCTAssertEqual(body["prompt_id"] as? Int, accepted)
                XCTAssertEqual(body["answer"] as? String, accepted == 1 ? "personal" : "")
                return (204, NSNull())
            default: XCTFail("Unexpected sign-in request"); return (404, [:])
            }
        }
        let session = ProviderLoginSession(api: harness.client)
        let observation = session.$display.compactMap { $0.prompt?.id }.removeDuplicates().sink { id in
            Task { @MainActor in
                if id == 1 { session.submit(promptID: id, answer: "My account") } // Labels aren't IDs.
                session.submit(promptID: id, answer: id == 1 ? "personal" : "")
                session.submit(promptID: id, answer: "duplicate")
            }
        }
        defer { observation.cancel(); session.abandon() }
        session.start(provider: "example", method: "api_key") { connected.fulfill() }
        await fulfillment(of: [connected], timeout: 2)
    }

    func testRemovingTheViewDeletesALateStartReplyWithoutPolling() async throws {
        let started = expectation(description: "Appearance started sign-in")
        let removed = expectation(description: "Login view removed")
        let deleted = expectation(description: "Late session deleted")
        let releaseStart = DispatchSemaphore(value: 0)
        let harness = try APITestHarness { request in
            switch (request.httpMethod, request.url?.path) {
            case ("POST", "/v1/providers/example/logins"):
                started.fulfill()
                XCTAssertEqual(releaseStart.wait(timeout: .now() + 5), .success)
                return (201, ["login_id": "late"])
            case ("DELETE", "/v1/logins/late"): deleted.fulfill(); return (204, NSNull())
            default: XCTFail("A dismissed login should never begin polling"); return (404, [:])
            }
        }
        let view = ProviderLoginView(api: harness.client, provider: "example", method: "oauth", onFinish: { XCTFail() })
        let host = NSHostingView(rootView: AnyView(view.onDisappear { removed.fulfill() }))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 320, height: 200), styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = host
        defer { window.contentView = nil }
        host.layoutSubtreeIfNeeded()
        await fulfillment(of: [started], timeout: 2)
        host.rootView = AnyView(EmptyView())
        host.layoutSubtreeIfNeeded()
        await fulfillment(of: [removed], timeout: 2)
        releaseStart.signal()
        await fulfillment(of: [deleted], timeout: 2)
    }

    func testDisappearanceDuringALongPollClearsThePromptAndIgnoresLateCompletion() async throws {
        let pending = expectation(description: "Polling while waiting for a secret")
        let deleted = expectation(description: "Deleted the specific login once")
        let releasePoll = DispatchSemaphore(value: 0)
        var sentPrompt = false
        let harness = try APITestHarness { request in
            switch (request.httpMethod, request.url?.path) {
            case ("POST", "/v1/providers/example/logins"): return (201, ["login_id": "cancel-me"])
            case ("POST", "/v1/logins/cancel-me/next-event"):
                if !sentPrompt {
                    sentPrompt = true
                    return (200, ["type": "prompt", "id": 1, "kind": "secret", "message": "Enter a key"])
                }
                pending.fulfill()
                XCTAssertEqual(releasePoll.wait(timeout: .now() + 3), .success)
                return (200, ["type": "done"])
            case ("DELETE", "/v1/logins/cancel-me"): deleted.fulfill(); return (204, NSNull())
            default: XCTFail("Unexpected sign-in request"); return (404, [:])
            }
        }
        let session = ProviderLoginSession(api: harness.client)
        session.start(provider: "example", method: "oauth") { XCTFail("Stale completion") }
        await fulfillment(of: [pending], timeout: 2)
        XCTAssertEqual(session.display.prompt?.kind, .secret)
        session.abandon()
        session.abandon() // Panel hide followed by view disappearance.
        XCTAssertNil(session.display.prompt)
        releasePoll.signal()
        await fulfillment(of: [deleted], timeout: 2)
    }

    func testExplicitCancelWaitsForTheLateLoginIDAndDeleteBeforeRefreshing() async throws {
        for status in [204, 409, 404] {
            let started = expectation(description: "Start in flight")
            let deleting = expectation(description: "DELETE in flight")
            let finished = expectation(description: "Refresh the actual connection")
            let releaseStart = DispatchSemaphore(value: 0), releaseDelete = DispatchSemaphore(value: 0)
            let harness = try APITestHarness { request in
                switch (request.httpMethod, request.url?.path) {
                case ("POST", "/v1/providers/example/logins"):
                    started.fulfill()
                    XCTAssertEqual(releaseStart.wait(timeout: .now() + 3), .success)
                    return (200, ["login_id": "late"])
                case ("DELETE", "/v1/logins/late"):
                    XCTAssertLessThanOrEqual(request.timeoutInterval, 10)
                    deleting.fulfill()
                    XCTAssertEqual(releaseDelete.wait(timeout: .now() + 3), .success)
                    return (status, ["detail": "private diagnostic"])
                default: XCTFail("A cancelled start should not poll"); return (404, [:])
                }
            }
            let session = ProviderLoginSession(api: harness.client)
            var didFinish = false
            session.start(provider: "example", method: "oauth") {
                didFinish = true
                finished.fulfill()
            }
            await fulfillment(of: [started], timeout: 2)
            session.cancel()
            releaseStart.signal()
            await fulfillment(of: [deleting], timeout: 2)
            session.cancel() // A repeated click must not issue another DELETE.
            XCTAssertFalse(didFinish)
            releaseDelete.signal()
            await fulfillment(of: [finished], timeout: 2)
            XCTAssertNil(session.display.message)
        }
    }

    func testFailedExplicitCancelCanRetryWithoutRestoringThePromptOrEchoingSecrets() async throws {
        let pending = expectation(description: "Polling with a secret prompt")
        let releasePoll = DispatchSemaphore(value: 0)
        let unconfirmed = expectation(description: "Cancellation could not be confirmed")
        let refreshed = expectation(description: "Retry confirmed")
        var sentPrompt = false, deletes = 0
        let harness = try APITestHarness { request in
            switch (request.httpMethod, request.url?.path) {
            case ("POST", "/v1/providers/example/logins"): return (200, ["login_id": "retry"])
            case ("POST", "/v1/logins/retry/next-event"):
                if !sentPrompt {
                    sentPrompt = true
                    return (200, ["type": "prompt", "id": 1, "kind": "secret", "message": "Enter key"])
                }
                pending.fulfill()
                XCTAssertEqual(releasePoll.wait(timeout: .now() + 3), .success)
                return (200, ["type": "done"])
            case ("DELETE", "/v1/logins/retry"):
                deletes += 1
                return (deletes == 1 ? 503 : 204, ["detail": "private diagnostic"])
            default: XCTFail("Unexpected request"); return (404, [:])
            }
        }
        let session = ProviderLoginSession(api: harness.client)
        let observation = session.$display.filter { $0.phase == .cancellationUnconfirmed }.sink { _ in unconfirmed.fulfill() }
        defer { observation.cancel() }
        var didFinish = false
        session.start(provider: "example", method: "api_key") { didFinish = true; refreshed.fulfill() }
        await fulfillment(of: [pending], timeout: 2)
        session.cancel()
        releasePoll.signal()
        await fulfillment(of: [unconfirmed], timeout: 2)
        XCTAssertFalse(didFinish)
        XCTAssertNil(session.display.prompt)
        XCTAssertEqual(session.display.message, "Couldn’t confirm cancellation. Sign-in may still finish.")
        session.cancel()
        await fulfillment(of: [refreshed], timeout: 2)
    }

    func testInvalidEventsAndBackendErrorsClearTheLoginWithoutEchoingDetails() async throws {
        let responses: [(Int, [String: Any])] = [
            (200, ["type": "auth_url", "url": "file:///tmp/test"]),
            (200, ["type": "auth_url", "url": "https://example%2Fother.test/"]),
            (200, ["type": "info", "message": "Open", "links": [["url": "https://user:password@example.test/"]]]),
            (200, ["type": "device_code", "userCode": "TEST-CODE"]),
            (200, ["type": "prompt", "kind": "select", "id": 1, "message": "Choose", "options": []]),
            (400, ["detail": "Request contained a private test answer"]),
        ]
        for response in responses {
            let deleted = expectation(description: "Failed login cleaned up")
            let harness = try APITestHarness { request in
                switch request.url?.path {
                case "/v1/providers/example/logins": return (201, ["login_id": "invalid"])
                case "/v1/logins/invalid/next-event": return response
                case "/v1/logins/invalid": XCTAssertEqual(request.httpMethod, "DELETE"); deleted.fulfill(); return (204, NSNull())
                default: XCTFail("Unexpected sign-in request"); return (404, [:])
                }
            }
            let session = ProviderLoginSession(api: harness.client)
            session.start(provider: "example", method: "oauth") { XCTFail() }
            await fulfillment(of: [deleted], timeout: 2)
            XCTAssertEqual(session.display.message, "Sign-in failed. Close and start again.")
            XCTAssertNil(session.display.prompt)
            XCTAssertNil(session.display.browserURL)
            XCTAssertTrue(session.display.links.isEmpty)
        }
    }

    private nonisolated static func body(_ request: URLRequest) throws -> [String: Any] {
        let json: Any
        if let data = request.httpBody { json = try JSONSerialization.jsonObject(with: data) }
        else {
            let stream = try XCTUnwrap(request.httpBodyStream)
            stream.open(); defer { stream.close() }
            json = try JSONSerialization.jsonObject(with: stream)
        }
        return try XCTUnwrap(json as? [String: Any])
    }
}
