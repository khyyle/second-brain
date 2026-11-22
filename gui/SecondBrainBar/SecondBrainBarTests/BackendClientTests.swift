import XCTest
@testable import SecondBrainBar

final class BackendClientTests: XCTestCase {
    func testInferenceStatusDecodesBothWorkersAndKeepsModelFailuresAsData() async throws {
        let harness = try APITestHarness { request in
            XCTAssertEqual(request.url?.path, "/v1/inference/status")
            return (200, ["worker_statuses": [
                "generation": NSNull(),
                "embedding": ["model": "nomic-embed-text", "phase": "error", "completed": 0,
                              "total": 0, "error": "Model download failed."],
            ]])
        }
        let status: InferenceStatusResponse = try await harness.client.request("/inference/status")
        XCTAssertEqual(Set(status.worker_statuses.keys), ["generation", "embedding"])
        XCTAssertNil(status.worker_statuses["generation"] ?? nil)
        let failure = try XCTUnwrap(status.worker_statuses["embedding"] ?? nil)
        XCTAssertEqual(failure.model, "nomic-embed-text")
        XCTAssertEqual(failure.phase, "error")
        XCTAssertEqual(failure.error, "Model download failed.")
    }

    func testRequestsUseLoopbackTokenAndEncodePathsAsQueryValues() async throws {
        let path = "documents/why? x & y#.md"
        let harness = try APITestHarness { request in
            XCTAssertEqual(request.url?.host, "127.0.0.1")
            XCTAssertEqual(request.url?.path, "/v1/sources/content")
            XCTAssertEqual(URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first?.value, path)
            XCTAssertTrue(request.value(forHTTPHeaderField: "Authorization")?.hasPrefix("Bearer ") == true)
            XCTAssertNil(request.value(forHTTPHeaderField: "X-API-Key"))
            return (200, ["markdown": "# Notes"])
        }
        let result: [String: String] = try await harness.client.request("/sources/content", query: ["raw_path": path])
        XCTAssertEqual(result["markdown"], "# Notes")
    }

    func testServiceErrorsAndValidationErrorsKeepTheirExplanations() async throws {
        for (status, detail) in [(409, "Build is using this source." as Any),
                                 (422, [["msg": "Enter a positive number"]] as Any)] {
            let harness = try APITestHarness { _ in (status, ["detail": detail]) }
            do { try await harness.client.send("/settings", method: "PATCH", body: [:]); XCTFail("Expected failure") }
            catch { XCTAssertTrue(error.localizedDescription.contains(status == 409 ? "Build is using" : "positive number")) }
        }
    }

    func testNoContentDeleteDoesNotNeedAJSONBody() async throws {
        let harness = try APITestHarness { request in
            XCTAssertEqual(request.httpMethod, "DELETE")
            return (204, NSNull())
        }
        let result = try await harness.client.send("/sources", method: "DELETE", query: ["raw_path": "documents/a.md"])
        XCTAssertTrue(result.isEmpty)
    }

    func testMalformedSuccessIsReportedInsteadOfPretendingDataIsEmpty() async throws {
        let harness = try APITestHarness { _ in (200, ["incorrect": true]) }
        do { let _: JobPage = try await harness.client.request("/jobs"); XCTFail("Expected decoding failure") }
        catch { XCTAssertTrue(error.localizedDescription.contains("incompatible response")) }
    }

    func testMissingSnapshotFieldsNameTheEndpointFieldAndRestartAction() async throws {
        for field in ["inference", "inference.worker_statuses"] {
            let harness = try APITestHarness { _ in
                var snapshot = APIFixtures.desktop()
                if field == "inference" { snapshot.removeValue(forKey: "inference") }
                else { snapshot["inference"] = [:] as [String: Any] }
                return (200, snapshot)
            }
            do {
                let _: DesktopSnapshot = try await harness.client.request("/desktop-state")
                XCTFail("Expected an incompatible response")
            } catch {
                XCTAssertEqual(error.localizedDescription,
                    "The backend returned an incompatible response for /desktop-state. Missing field: \(field). Update and restart the app and backend together.")
            }
        }
    }

    func testStartupEnvironmentSelectsTheSameVaultAndPortAsPython() {
        let config = AppConfig.fromEnvironment(["SECOND_BRAIN_VAULT": "/tmp/example-vault", "SECOND_BRAIN_PORT": "9876"])
        XCTAssertEqual(config.vaultRoot.path, "/tmp/example-vault")
        XCTAssertEqual(config.baseURL.absoluteString, "http://127.0.0.1:9876/v1")
    }
}
