import Foundation

/// Resolves the on-disk locations the menu-bar app needs to talk to:
/// the vault root (under which `drops/`, `wiki/`, and `manifest.db`
/// live) and an optional path to the project's `run.sh` for the
/// "Run pipeline" action.
///
/// The vault defaults to `~/second-brain`, matching the `data_dir`
/// default in the Python `config.py`. The pipeline script location is
/// not hardcoded: `install.sh` records the absolute path of `run.sh`
/// into `~/second-brain/.pipeline-script`, and we read it from there.
/// If that pointer is missing or stale, `runScriptPath` is nil and the
/// run action is disabled.
struct AppConfig {
    let vaultRoot: URL
    let runScriptPath: URL?

    static let `default`: AppConfig = {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let vault = home.appending(path: "second-brain")
        return AppConfig(
            vaultRoot: vault,
            runScriptPath: Self.readPipelineScript(vault: vault)
        )
    }()

    /// Read the pipeline-script path recorded by the installer, returning
    /// it only if the file it points to still exists.
    private static func readPipelineScript(vault: URL) -> URL? {
        let pointer = vault.appending(path: ".pipeline-script")
        guard let recorded = try? String(contentsOf: pointer, encoding: .utf8) else {
            return nil
        }
        let path = recorded.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !path.isEmpty, FileManager.default.fileExists(atPath: path) else {
            return nil
        }
        return URL(fileURLWithPath: path)
    }

    var dropsRoot: URL { vaultRoot.appending(path: "drops") }
    var wikiRoot:  URL { vaultRoot.appending(path: "wiki") }
    var rawRoot:   URL { vaultRoot.appending(path: "raw") }
    var inboxRoot: URL { vaultRoot.appending(path: "inbox") }
    var manifestDB: URL { vaultRoot.appending(path: "manifest.db") }
    var statusFile: URL { vaultRoot.appending(path: ".status.json") }
    var buildLog: URL { vaultRoot.appending(path: ".build-log.jsonl") }
    var clusterPlanFile: URL { vaultRoot.appending(path: ".clusters.json") }
    var clusterOverridesFile: URL { vaultRoot.appending(path: ".cluster-overrides.json") }

    /// Project root, derived from the installer's recorded pipeline-script
    /// pointer. nil when the pointer is missing.
    var repoDir: URL? { runScriptPath?.deletingLastPathComponent() }

    /// The pipeline's `config/config.yaml`, the single source of truth the
    /// Python side loads. Settings edits target this file in place.
    var configFile: URL? { repoDir?.appending(path: "config").appending(path: "config.yaml") }
}
