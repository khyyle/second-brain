import XCTest
@testable import SecondBrainBar

/// The Build tab offers the saved provider's models and saves the one chosen.
@MainActor
final class BuildModelTests: XCTestCase {
    private let sonnet = ["id": "sonnet", "name": "Sonnet"]

    /// A backend with one provider saved in Settings. Tests change what it offers between reads.
    private final class Backend {
        var provider = "vendor"
        var model = "sonnet"
        var models: [[String: String]] = []
        var connected = true
        var failure: (Int, String)?
        var saves: [[String: Any]] = []

        func respond(to request: URLRequest) throws -> (Int, Any) {
            switch (request.httpMethod, request.url?.path) {
            case ("GET", "/v1/desktop-state"):
                var snapshot = APIFixtures.desktop()
                snapshot["compilation_provider"] = provider
                snapshot["compilation_model"] = model
                return (200, snapshot)
            case ("GET", "/v1/providers/\(provider)/models"):
                if let failure { return (failure.0, ["detail": failure.1]) }
                return (200, models)
            case ("GET", "/v1/providers/\(provider)/connection"): return (200, ["connected": connected])
            case ("PATCH", "/v1/settings"):
                if let failure { return (failure.0, ["detail": failure.1]) }
                let body = try XCTUnwrap(JSONSerialization.jsonObject(with: Self.body(request)) as? [String: Any])
                saves.append(body)
                model = try XCTUnwrap((body["compilation"] as? [String: String])?["model"])
                return (200, [:])
            default:
                XCTFail("Unexpected request: \(request.httpMethod ?? "") \(request.url?.path ?? "")")
                return (404, [:])
            }
        }

        private static func body(_ request: URLRequest) throws -> Data {
            if let data = request.httpBody { return data }
            let stream = try XCTUnwrap(request.httpBodyStream)
            stream.open(); defer { stream.close() }
            var data = Data(), buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                data.append(buffer, count: count)
            }
            return data
        }
    }

    private func store(_ backend: Backend) async throws -> (PipelineStore, APITestHarness) {
        let harness = try APITestHarness { try backend.respond(to: $0) }
        let store = PipelineStore(config: harness.config, api: harness.client)
        await store.refreshNow()
        return (store, harness)
    }

    func testOnlyAModelTheConnectedProviderOffersCanBuild() async throws {
        let backend = Backend()
        let (store, harness) = try await store(backend)
        _ = harness
        XCTAssertEqual(store.buildModels, .loading)
        XCTAssertTrue(store.hasBuildModel, "The saved choice stands until the first read says otherwise.")

        backend.models = [sonnet]
        await store.refreshBuildModels()
        XCTAssertEqual(store.buildModels, .available([ModelChoiceResponse(id: "sonnet", name: "Sonnet")]))
        XCTAssertTrue(store.hasBuildModel)

        backend.models = [["id": "opus", "name": "Opus"]] // The account no longer lists the saved model.
        await store.refreshBuildModels()
        XCTAssertFalse(store.hasBuildModel)

        backend.models = [] // Connected, but this account has no models.
        await store.refreshBuildModels()
        XCTAssertEqual(store.buildModels, .available([]))
        XCTAssertFalse(store.hasBuildModel)

        backend.connected = false
        await store.refreshBuildModels()
        XCTAssertEqual(store.buildModels, .disconnected)
        XCTAssertFalse(store.hasBuildModel)

        backend.failure = (503, "Could not start the provider helper.")
        await store.refreshBuildModels()
        XCTAssertEqual(store.buildModels, .failed("Could not start the provider helper."))
        XCTAssertFalse(store.hasBuildModel)
    }

    func testAnotherProviderStartsWithoutTheLastProvidersModels() async throws {
        let backend = Backend()
        backend.models = [sonnet]
        let (store, harness) = try await store(backend)
        _ = harness
        await store.refreshBuildModels()
        XCTAssertTrue(store.hasBuildModel)

        // Saving a different provider in Settings clears the model chosen for the old one.
        backend.provider = "other"
        backend.model = ""
        backend.models = [["id": "flash", "name": "Flash"]]
        await store.refreshNow()
        XCTAssertFalse(store.hasBuildModel)
        await store.refreshBuildModels()
        XCTAssertEqual(store.buildModels, .available([ModelChoiceResponse(id: "flash", name: "Flash")]))
        XCTAssertFalse(store.hasBuildModel)

        store.forgetBuildModels() // Returning from Settings: reread rather than trust the old answer.
        XCTAssertEqual(store.buildModels, .loading)
    }

    func testChoosingAModelSavesOnlyTheModelAndShowsItAtOnce() async throws {
        let backend = Backend()
        backend.models = [sonnet, ["id": "opus", "name": "Opus"]]
        let (store, harness) = try await store(backend)
        _ = harness
        await store.refreshBuildModels()

        let saved = expectation(description: "Snapshot confirms the choice")
        store.selectBuildModel("opus")
        XCTAssertEqual(store.compilationModel, "opus")
        let confirmation = store.$connected.dropFirst().sink { _ in saved.fulfill() }
        await fulfillment(of: [saved], timeout: 2)
        confirmation.cancel()
        XCTAssertEqual(backend.saves.count, 1)
        XCTAssertEqual(backend.saves.first?["compilation"] as? [String: String], ["model": "opus"])
        XCTAssertEqual(store.compilationModel, "opus")
        XCTAssertTrue(store.hasBuildModel)

        store.selectBuildModel("opus") // Choosing the current model again writes nothing.
        XCTAssertEqual(backend.saves.count, 1)
    }

    func testAFailedSaveRestoresThePreviousModelAndSaysWhy() async throws {
        let backend = Backend()
        backend.models = [sonnet, ["id": "opus", "name": "Opus"]]
        let (store, harness) = try await store(backend)
        _ = harness
        await store.refreshBuildModels()
        backend.failure = (503, "Settings could not be saved.")

        let rejected = expectation(description: "Save rejected")
        let observation = store.$actionError.compactMap { $0 }.sink { _ in rejected.fulfill() }
        store.selectBuildModel("opus")
        await fulfillment(of: [rejected], timeout: 2)
        observation.cancel()
        XCTAssertEqual(store.actionError, "Settings could not be saved.")
        XCTAssertEqual(store.compilationModel, "sonnet")
    }
}
