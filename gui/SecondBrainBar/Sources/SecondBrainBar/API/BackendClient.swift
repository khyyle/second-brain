import Foundation

struct BackendError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Loopback-only transport. Never forwards the vault token through a redirect.
final class BackendClient {
    let config: AppConfig
    private let session: URLSession
    private static let redirects = RejectRedirects()
    private static let sharedSession: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 120
        configuration.httpShouldSetCookies = false
        configuration.connectionProxyDictionary = [:]
        return URLSession(configuration: configuration, delegate: redirects, delegateQueue: nil)
    }()
    private static let eventSession: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 45 // Server sends keepalives every 15 seconds.
        configuration.timeoutIntervalForResource = 7 * 24 * 60 * 60
        configuration.httpShouldSetCookies = false
        configuration.connectionProxyDictionary = [:]
        return URLSession(configuration: configuration, delegate: redirects, delegateQueue: nil)
    }()

    init(config: AppConfig, session: URLSession? = nil) {
        self.config = config
        self.session = session ?? Self.sharedSession
    }

    func request<T: Decodable>(_ path: String, method: String = "GET",
                               query: [String: String] = [:], body: [String: Any]? = nil) async throws -> T {
        let data = try await send(path, method: method, query: query, body: body)
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw BackendError(message: "The backend returned an incompatible response. Update and restart the app and backend together.") }
    }

    @discardableResult
    func send(_ path: String, method: String = "GET", query: [String: String] = [:],
              body: [String: Any]? = nil) async throws -> Data {
        var request = try authorizedRequest(path, query: query)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw BackendError(message: "The local backend did not return an HTTP response.")
        }
        guard (200...299).contains(http.statusCode) else {
            let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let validation = (object?["detail"] as? [[String: Any]])?.compactMap { $0["msg"] as? String }.joined(separator: "; ")
            let detail = object?["detail"] as? String ?? validation
            throw BackendError(message: detail ?? "Backend request failed (HTTP \(http.statusCode)).")
        }
        return data
    }

    /// Streams snapshots until disconnect or cancellation, then closes the connection.
    @MainActor
    func receiveSnapshots(_ receive: (DesktopSnapshot) -> Void) async throws {
        var request = try authorizedRequest("/events")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        let (bytes, response) = try await Self.eventSession.bytes(for: request)
        defer { bytes.task.cancel() }
        guard let http = response as? HTTPURLResponse, http.statusCode == 200,
              http.mimeType == "text/event-stream" else {
            throw BackendError(message: "Could not connect to backend updates. Restart the app and backend together.")
        }
        var parser = ServerEventParser()
        // AsyncBytes.lines skips empty lines on supported macOS versions. SSE
        // needs those blank delimiters, so preserve them while decoding UTF-8.
        var lineBytes: [UInt8] = []
        try await withTaskCancellationHandler {
            for try await byte in bytes {
                try Task.checkCancellation()
                guard byte == 10 else { lineBytes.append(byte); continue }
                if lineBytes.last == 13 { lineBytes.removeLast() }
                let line = String(decoding: lineBytes, as: UTF8.self)
                lineBytes.removeAll(keepingCapacity: true)
                guard let event = parser.consume(line) else { continue }
                switch event.name {
                case "snapshot": receive(try JSONDecoder().decode(DesktopSnapshot.self, from: event.data))
                case "unavailable":
                    let details = try? JSONDecoder().decode([String: String].self, from: event.data)
                    throw BackendError(message: details?["detail"] ?? "The backend couldn't refresh the library.")
                default: break
                }
            }
        } onCancel: {
            bytes.task.cancel()
        }
        throw BackendError(message: "Backend updates disconnected. Reconnecting…")
    }

    private func authorizedRequest(_ path: String, query: [String: String] = [:]) throws -> URLRequest {
        guard (1...65535).contains(config.port), path.hasPrefix("/"), !path.contains(".."), !path.contains("?") else {
            throw BackendError(message: "Invalid local backend address.")
        }
        var components = URLComponents(url: config.baseURL, resolvingAgainstBaseURL: false)!
        components.path += path
        components.queryItems = query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        var request = URLRequest(url: components.url!)
        let token: String
        do { token = try String(contentsOf: config.tokenFile, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines) }
        catch { throw BackendError(message: "The local backend hasn't started for this vault yet.") }
        guard !token.isEmpty else { throw BackendError(message: "The backend token is empty. Restart the backend.") }
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return request
    }
}

private final class RejectRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

func backendDate(_ value: String?) -> Date? {
    guard let value else { return nil }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value)
}
