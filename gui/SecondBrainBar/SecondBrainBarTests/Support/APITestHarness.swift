import Foundation
import XCTest
@testable import SecondBrainBar

/// Every test gets its own vault token, URLSession, and route handler. No live HTTP.
final class APITestHarness {
    let config: AppConfig
    let client: BackendClient
    let token = UUID().uuidString
    private let session: URLSession

    init(handler: @escaping (URLRequest) throws -> (Int, Any)) throws {
        let vault = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        config = AppConfig(vaultRoot: vault)
        try FileManager.default.createDirectory(at: config.tokenFile.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(token.utf8).write(to: config.tokenFile)
        HTTPStub.register(token, handler: handler)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [HTTPStub.self]
        session = URLSession(configuration: configuration)
        client = BackendClient(config: config, session: session)
    }

    deinit {
        session.invalidateAndCancel()
        HTTPStub.unregister(token)
        try? FileManager.default.removeItem(at: config.vaultRoot)
    }
}

final class HTTPStub: URLProtocol {
    typealias Handler = (URLRequest) throws -> (Int, Any)
    private static let lock = NSLock()
    private static var handlers: [String: Handler] = [:]
    static func register(_ token: String, handler: @escaping Handler) {
        lock.lock(); defer { lock.unlock() }
        handlers[token] = handler
    }
    static func unregister(_ token: String) {
        lock.lock(); defer { lock.unlock() }
        handlers.removeValue(forKey: token)
    }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let token = String((request.value(forHTTPHeaderField: "Authorization") ?? "").dropFirst(7))
        Self.lock.lock()
        let handler = Self.handlers[token]
        Self.lock.unlock()
        do {
            guard let handler else { throw BackendError(message: "Unexpected request without this test's token") }
            let (status, json) = try handler(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil,
                                           headerFields: ["Content-Type": "application/json"])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            if status != 204 { client?.urlProtocol(self, didLoad: try JSONSerialization.data(withJSONObject: json, options: [.fragmentsAllowed])) }
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

enum APIFixtures {
    static func desktop(staged: [String] = [], jobs: [[String: Any]] = []) -> [String: Any] {
        ["state": state(staged: staged), "jobs": jobs, "imports": [], "triage": [],
         "grouping_plan": NSNull(), "build_actions": [], "compilation_model": "test-model"]
    }
    static let timestamp = "2026-10-23T12:00:00.000000Z"
    static func job(id: String = "build-1", kind: String = "build", status: String = "running",
                    result: [String: Any] = [:], error: String = "") -> [String: Any] {
        ["id": id, "kind": kind, "lane": kind == "ingest" ? "ingest" : "build", "status": status,
         "error": error, "result": result, "cancel_requested": false, "created_at": timestamp,
         "started_at": timestamp, "finished_at": status == "running" ? NSNull() : timestamp as Any,
         "heartbeat_at": timestamp]
    }
    static func state(staged: [String] = []) -> [String: Any] {
        ["staged": staged.map { ["rel": $0] }, "built_count": 2, "costs": [:], "stale": false,
         "needs_review": 0, "skipped": 0, "ingest_failures": [:], "generated_at": timestamp, "pending_triage": []]
    }
    static func importFile(path: String = "/vault/drops/documents/a.md", status: String = "pending") -> [String: Any] {
        ["file_path": path, "status": status, "updated_at": timestamp, "error": NSNull()]
    }
}
