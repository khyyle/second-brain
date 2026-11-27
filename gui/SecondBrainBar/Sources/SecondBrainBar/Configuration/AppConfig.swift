import Foundation

/// Startup coordinates only. Preferences and all vault state belong to the API.
struct AppConfig {
    let vaultRoot: URL
    var port: Int = 8000

    static let `default` = fromEnvironment()

    static func fromEnvironment(_ env: [String: String] = ProcessInfo.processInfo.environment) -> AppConfig {
        let path = (env["SECOND_BRAIN_VAULT"] ?? "~/second-brain") as NSString
        let vault = URL(fileURLWithPath: path.expandingTildeInPath).standardizedFileURL
        return AppConfig(vaultRoot: vault, port: Int(env["SECOND_BRAIN_PORT"] ?? "8000") ?? 8000)
    }

    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)/v1")! }
    var tokenFile: URL { vaultRoot.appending(path: ".backend/api-token") }
    var dropsRoot: URL { vaultRoot.appending(path: "drops") }
    var wikiRoot: URL { vaultRoot.appending(path: "wiki") }
    var rawRoot: URL { vaultRoot.appending(path: "raw") }
}
