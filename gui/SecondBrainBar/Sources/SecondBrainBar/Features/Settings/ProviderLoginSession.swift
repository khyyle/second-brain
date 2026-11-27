import Combine
import Foundation

/// One Settings sign-in. Pi can finish in the browser while a prompt is still on screen.
@MainActor
final class ProviderLoginSession: ObservableObject {
    enum Phase { case signingIn, cancelling, cancellationUnconfirmed, closed }

    struct Display {
        var phase: Phase = .signingIn
        var message: String? = "Starting sign-in…"
        var browserURL: URL?
        var instructions: String?
        var device: (code: String, verificationURL: URL)?
        var info: String?
        var links: [LoginLink] = []
        var prompt: LoginPrompt?
    }

    // The single panel owns the login; Quit only needs a handle to that session's cleanup.
    private(set) static weak var active: ProviderLoginSession?
    @Published private(set) var display = Display()
    private let api: BackendClient
    private var sessionPath: String?
    private var operation: Task<Void, Never>?
    private var answering: Task<Void, Never>?
    private var onFinish: (() -> Void)?
    private static let controlTimeout: TimeInterval = 10 // The backend allows three seconds for DELETE.

    init(api: BackendClient) { self.api = api }

    func start(provider: String, method: String, onFinish: @escaping () -> Void) {
        guard display.phase == .signingIn, operation == nil else { return }
        Self.active = self
        self.onFinish = onFinish
        operation = Task {
            // A cancel before the start request went out has nothing to delete.
            if display.phase == .cancelling { finish(); return }
            guard display.phase == .signingIn else { return }
            do {
                guard Self.isPathSegment(provider), !method.isBlank else { throw InvalidLoginEvent() }
                struct Started: Decodable { let login_id: String }
                let start: Started = try await api.request("/providers/\(provider)/logins", method: "POST", body: ["method": method], timeout: Self.controlTimeout)
                guard Self.isPathSegment(start.login_id) else { throw InvalidLoginEvent() }
                let path = "/logins/\(start.login_id)"
                guard display.phase != .closed else {
                    _ = try? await api.send(path, method: "DELETE", timeout: Self.controlTimeout)
                    return
                }
                sessionPath = path
                if display.phase == .cancelling { await confirmCancellation(path); return }
                display.message = "Waiting for the provider…"
                while display.phase == .signingIn {
                    let event: LoginEvent = try await api.request("\(path)/next-event", method: "POST", timeout: 360)
                    guard display.phase == .signingIn else { return }
                    switch event {
                    case let .authURL(url, instructions):
                        display.browserURL = url
                        display.instructions = instructions
                        display.message = nil
                    case let .deviceCode(code, url):
                        display.device = (code, url)
                        display.message = nil
                    case let .info(message, links):
                        display.info = message
                        display.links = links
                        display.message = nil
                    case let .progress(message):
                        display.message = message
                    case let .prompt(prompt):
                        answering?.cancel()
                        display.prompt = prompt
                        display.message = nil
                    case .done, .cancelled:
                        finish()
                    }
                }
            } catch {
                guard !Task.isCancelled, display.phase != .closed else { return }
                let awaitingLoginID = display.phase == .cancelling
                abandon()
                // Without a login ID we can't retry DELETE. Close refreshes the connection.
                display.message = awaitingLoginID ? "Couldn’t confirm cancellation. Close to check the connection."
                                                  : "Sign-in failed. Close and start again."
            }
        }
    }

    func submit(promptID: Int, answer: String) {
        guard let path = sessionPath, let prompt = display.prompt,
              prompt.id == promptID, prompt.accepts(answer) else { return }
        display.prompt = nil // Consume before yielding; don't send an answer twice.
        display.message = "Waiting for the provider…"
        answering = Task {
            do {
                try Task.checkCancellation()
                try await api.send("\(path)/answers", method: "POST", body: ["prompt_id": prompt.id, "answer": answer])
            } catch {
                guard !Task.isCancelled, display.phase == .signingIn else { return }
                display.prompt = prompt
                display.message = "Couldn’t submit the answer. Try again or cancel."
            }
        }
    }

    func cancel() {
        guard display.phase == .signingIn || display.phase == .cancellationUnconfirmed else { return }
        display = Display(phase: .cancelling, message: "Cancelling sign-in…")
        answering?.cancel()
        // If start is in flight, its reply supplies the ID and continues cancellation above.
        if let path = sessionPath {
            operation?.cancel()
            operation = Task { await confirmCancellation(path) }
        }
    }

    private func confirmCancellation(_ path: String) async {
        do {
            try await api.send(path, method: "DELETE", timeout: Self.controlTimeout)
        } catch let error as BackendError where error.statusCode == 404 || error.statusCode == 409 {
            // The login expired or raced with completion. Refresh; don't report it as deleted.
        } catch {
            guard display.phase == .cancelling else { return }
            display = Display(phase: .cancellationUnconfirmed, message: "Couldn’t confirm cancellation. Sign-in may still finish.")
            return
        }
        if display.phase == .cancelling { finish() }
    }

    private func finish() {
        let notify = onFinish
        sessionPath = nil
        operation = nil
        abandon()
        notify?()
    }

    /// Views leave immediately. Quit can await the same cleanup, including a late start reply.
    @discardableResult
    func abandon() -> Task<Void, Never>? {
        guard display.phase != .closed else { return operation }
        answering?.cancel()
        answering = nil
        if let path = sessionPath, display.phase != .cancelling {
            operation?.cancel()
            operation = Task { [api] in _ = try? await api.send(path, method: "DELETE", timeout: Self.controlTimeout) }
        }
        // A pending start knows how to delete its late ID. An existing DELETE already owns it.
        if let outstanding = operation {
            operation = Task { await outstanding.value; operation = nil }
        }
        sessionPath = nil
        onFinish = nil
        display = Display(phase: .closed, message: nil)
        return operation
    }

    private static func isPathSegment(_ id: String) -> Bool {
        !id.isEmpty && id != "." && !id.contains("..") && id.allSatisfy {
            $0.isASCII && ($0.isLetter || $0.isNumber || "-_.".contains($0))
        }
    }
}

private struct InvalidLoginEvent: Error {}

private extension String {
    var isBlank: Bool { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
}

private func loginURL(_ raw: String) throws -> URL {
    guard !raw.contains("\\"), raw.rangeOfCharacter(from: .whitespacesAndNewlines.union(.controlCharacters)) == nil,
          let parts = URLComponents(string: raw), parts.scheme?.lowercased() == "https",
          parts.user == nil, parts.password == nil, (1...65535).contains(parts.port ?? 443),
          let url = parts.url, let host = url.host, !host.isEmpty,
          host.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || "-.:".contains($0)) })
    else { throw InvalidLoginEvent() }
    return url
}

struct LoginLink: Decodable {
    let url: URL
    let label: String?

    private enum Key: CodingKey { case url, label }
    init(from decoder: Decoder) throws {
        let fields = try decoder.container(keyedBy: Key.self)
        url = try loginURL(fields.decode(String.self, forKey: .url))
        label = try fields.decodeIfPresent(String.self, forKey: .label)
    }
}

struct LoginPrompt: Decodable, Identifiable {
    enum Kind: String, Decodable { case text, secret, manualCode = "manual_code", select }
    struct Option: Decodable, Identifiable { let id, label: String; let description: String? }
    let id: Int
    let kind: Kind
    let message: String
    let callback_listening: Bool?
    let placeholder: String?
    let options: [Option]?

    fileprivate var isValid: Bool {
        guard id > 0, !message.isBlank else { return false }
        guard kind == .select else { return true }
        guard let options, !options.isEmpty else { return false }
        return options.allSatisfy { !$0.id.isBlank && !$0.label.isBlank } && Set(options.map(\.id)).count == options.count
    }

    func accepts(_ answer: String) -> Bool {
        switch kind {
        case .text: return true // An optional account or region can be left empty.
        case .secret, .manualCode: return !answer.isBlank
        case .select: return options?.contains { $0.id == answer } == true
        }
    }
}

private enum LoginEvent: Decodable {
    case authURL(URL, String?), deviceCode(String, URL), info(String, [LoginLink]), progress(String), prompt(LoginPrompt)
    case done, cancelled

    private enum Key: CodingKey { case type, url, instructions, userCode, verificationUri, message, links }

    init(from decoder: Decoder) throws {
        let fields = try decoder.container(keyedBy: Key.self)
        func text(_ key: Key) throws -> String {
            let value = try fields.decode(String.self, forKey: key)
            guard !value.isBlank else { throw InvalidLoginEvent() }
            return value
        }
        switch try text(.type) {
        case "auth_url": self = try .authURL(loginURL(text(.url)), fields.decodeIfPresent(String.self, forKey: .instructions))
        case "device_code": self = try .deviceCode(text(.userCode), loginURL(text(.verificationUri)))
        case "info": self = try .info(text(.message), fields.decodeIfPresent([LoginLink].self, forKey: .links) ?? [])
        case "progress": self = try .progress(text(.message))
        case "prompt":
            let prompt = try LoginPrompt(from: decoder)
            guard prompt.isValid else { throw InvalidLoginEvent() }
            self = .prompt(prompt)
        case "done": self = .done
        case "cancelled": self = .cancelled
        default: throw InvalidLoginEvent()
        }
    }
}
