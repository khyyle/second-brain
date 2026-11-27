import AppKit
import XCTest
@testable import SecondBrainBar

@MainActor
final class StatusBarControllerTests: XCTestCase {
    func testQuitWaitsForThePanelsLateLoginIDAndOneDelete() async throws {
        let controller = StatusBarController()
        XCTAssertEqual(controller.applicationShouldTerminate(.shared), .terminateNow)
        let started = expectation(description: "Login start in flight")
        let deleting = expectation(description: "Deleting this panel’s login")
        let replied = expectation(description: "Quit allowed after DELETE")
        let releaseStart = DispatchSemaphore(value: 0), releaseDelete = DispatchSemaphore(value: 0)
        let harness = try APITestHarness { request in
            switch (request.httpMethod, request.url?.path) {
            case ("POST", "/v1/providers/example/logins"):
                started.fulfill()
                XCTAssertEqual(releaseStart.wait(timeout: .now() + 3), .success)
                return (200, ["login_id": "this-panel"])
            case ("DELETE", "/v1/logins/this-panel"):
                deleting.fulfill()
                XCTAssertEqual(releaseDelete.wait(timeout: .now() + 3), .success)
                return (503, [:]) // Quit still proceeds; don't loop on failure.
            default: XCTFail("Unexpected request during Quit"); return (404, [:])
            }
        }
        let login = ProviderLoginSession(api: harness.client)
        login.start(provider: "example", method: "oauth") { XCTFail("A quitting view should not refresh") }
        await fulfillment(of: [started], timeout: 2)

        var didReply = false
        XCTAssertEqual(controller.prepareToQuit { didReply = true; replied.fulfill() }, .terminateLater)
        login.abandon() // View disappearance and a second Quit must share the same cleanup.
        XCTAssertEqual(controller.prepareToQuit { XCTFail("Second termination reply") }, .terminateLater)
        releaseStart.signal()
        await fulfillment(of: [deleting], timeout: 2)
        XCTAssertFalse(didReply)
        releaseDelete.signal()
        await fulfillment(of: [replied], timeout: 2)
        XCTAssertEqual(controller.applicationShouldTerminate(.shared), .terminateNow)
    }

    func testQuitJoinsAnExplicitCancellationAlreadyOnTheWire() async throws {
        let polling = expectation(description: "Login ID received")
        let deleting = expectation(description: "Explicit Cancel sent DELETE")
        let replied = expectation(description: "Quit allowed")
        let releasePoll = DispatchSemaphore(value: 0), releaseDelete = DispatchSemaphore(value: 0)
        let harness = try APITestHarness { request in
            switch (request.httpMethod, request.url?.path) {
            case ("POST", "/v1/providers/example/logins"): return (200, ["login_id": "mine"])
            case ("POST", "/v1/logins/mine/next-event"):
                polling.fulfill()
                XCTAssertEqual(releasePoll.wait(timeout: .now() + 3), .success)
                return (200, ["type": "cancelled"])
            case ("DELETE", "/v1/logins/mine"):
                deleting.fulfill()
                XCTAssertEqual(releaseDelete.wait(timeout: .now() + 3), .success)
                return (204, NSNull())
            default: XCTFail("Unexpected request"); return (404, [:])
            }
        }
        let login = ProviderLoginSession(api: harness.client)
        login.start(provider: "example", method: "oauth") { XCTFail("A quitting view should not refresh") }
        await fulfillment(of: [polling], timeout: 2)
        login.cancel()
        releasePoll.signal()
        await fulfillment(of: [deleting], timeout: 2)
        let controller = StatusBarController()
        let earlyReply = expectation(description: "Quit must not finish while DELETE is pending")
        earlyReply.isInverted = true
        var holdingDelete = true
        XCTAssertEqual(controller.prepareToQuit {
            if holdingDelete { earlyReply.fulfill() }
            replied.fulfill()
        }, .terminateLater)
        await fulfillment(of: [earlyReply], timeout: 0.1)
        holdingDelete = false
        releaseDelete.signal()
        await fulfillment(of: [replied], timeout: 2)
    }

    func testQuitDeadlineDoesNotWaitForAnUnresponsiveStart() async throws {
        let started = expectation(description: "Login start in flight")
        let replied = expectation(description: "Quit deadline reached")
        let release = DispatchSemaphore(value: 0)
        let harness = try APITestHarness { request in
            XCTAssertEqual(request.url?.path, "/v1/providers/example/logins")
            started.fulfill()
            XCTAssertEqual(release.wait(timeout: .now() + 3), .success)
            throw URLError(.timedOut) // No ID to delete.
        }
        let login = ProviderLoginSession(api: harness.client)
        login.start(provider: "example", method: "oauth") { XCTFail("A quitting view should not refresh") }
        await fulfillment(of: [started], timeout: 2)
        let controller = StatusBarController()
        XCTAssertEqual(controller.prepareToQuit(timeout: 0) { replied.fulfill() }, .terminateLater)
        await fulfillment(of: [replied], timeout: 2)
        release.signal()
        await login.abandon()?.value
    }
}
