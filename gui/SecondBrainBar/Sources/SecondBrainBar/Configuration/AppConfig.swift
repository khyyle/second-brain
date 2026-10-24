import Foundation

/// Startup coordinates only. Preferences and all vault state belong to the API.
struct AppConfig {
    let vaultRoot: URL
    var port: Int = 8000
    var backendScript: URL? = nil

    static let `default` = fromEnvironment()

    static func fromEnvironment(_ env: [String: String] = ProcessInfo.processInfo.environment) -> AppConfig {
        let path = (env["SECOND_BRAIN_VAULT"] ?? "~/second-brain") as NSString
        let vault = URL(fileURLWithPath: path.expandingTildeInPath).standardizedFileURL
        let recorded = Bundle.main.url(forResource: "BackendLauncher", withExtension: "txt")
            .flatMap { try? String(contentsOf: $0, encoding: .utf8) }
        let script = env["SECOND_BRAIN_BACKEND_SCRIPT"] ?? recorded?.trimmingCharacters(in: .whitespacesAndNewlines)
        return AppConfig(vaultRoot: vault, port: Int(env["SECOND_BRAIN_PORT"] ?? "8000") ?? 8000,
                         backendScript: script.flatMap { $0.isEmpty ? nil : URL(fileURLWithPath: $0) })
    }

    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)/v1")! }
    var tokenFile: URL { vaultRoot.appending(path: ".backend/api-token") }
    var dropsRoot: URL { vaultRoot.appending(path: "drops") }
    var wikiRoot: URL { vaultRoot.appending(path: "wiki") }
    var rawRoot: URL { vaultRoot.appending(path: "raw") }
}
