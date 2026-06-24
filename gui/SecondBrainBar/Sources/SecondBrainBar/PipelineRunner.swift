import Foundation
import AppKit

/// Fire-and-forget shell-out for the "Run pipeline" action.
///
/// Detached so the menu-bar app's responsiveness is unaffected by the
/// Python pipeline's runtime. Output goes wherever `run.sh` directs
/// it (typically `~/second-brain/logs/pipeline.log`).
enum PipelineRunner {
    /// A pipeline stage. `drops` ingests only the drop folders; `ingest` is a
    /// full ingest including watched folders; `compile` is the paid build.
    enum Stage: String {
        case drops
        case ingest
        case compile
        case all
    }

    @discardableResult
    static func runDetached(scriptURL: URL, stage: Stage = .all) -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        // Quote the script path (it can contain spaces) and pass the stage arg.
        process.arguments = ["-l", "-c", "\"\(scriptURL.path)\" \(stage.rawValue)"]
        process.standardOutput = nil
        process.standardError = nil
        do {
            try process.run()
            return true
        } catch {
            return false
        }
    }

    static func revealInFinder(_ url: URL) {
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    /// Ask a running build to stop by dropping the `.stop` flag the
    /// compiler polls between sources and agent turns. Co-operative, so it
    /// halts cleanly rather than killing the process tree.
    static func requestStop(vaultRoot: URL) {
        let flag = vaultRoot.appending(path: ".stop")
        try? Data(ISO8601DateFormatter().string(from: Date()).utf8)
            .write(to: flag, options: .atomic)
    }

    /// Whether the given MCP client already has the "second-brain" server
    /// configured, so the UI can show a persistent "Connected" state.
    static func isMCPConfigured(_ target: String) -> Bool {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let path: URL
        switch target {
        case "claude-desktop":
            path = home.appending(path: "Library/Application Support/Claude/claude_desktop_config.json")
        case "cursor":
            path = home.appending(path: ".cursor/mcp.json")
        default:
            return false
        }
        guard let data = try? Data(contentsOf: path),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let servers = obj["mcpServers"] as? [String: Any]
        else { return false }
        return servers["second-brain"] != nil
    }

    /// launchd label the Python scheduler installs under.
    private static let scheduleLabel = "com.secondbrain.pipeline"

    /// Whether the launchd job is currently installed (its plist exists).
    static var scheduleInstalled: Bool {
        let plist = FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Library/LaunchAgents/\(scheduleLabel).plist")
        return FileManager.default.fileExists(atPath: plist.path)
    }

    private static func managedProcess(repoDir: URL, command: String) -> Process {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        let script = "cd \"\(repoDir.path)\" && "
            + "export PATH=\"$HOME/.local/bin:$PATH\" && "
            + "uv run second-brain \(command)"
        process.arguments = ["-l", "-c", script]
        process.standardOutput = nil
        process.standardError = nil
        return process
    }

    /// Run a `second-brain` management subcommand (e.g. "schedule install")
    /// from the project directory, detached. The Bool only reports that the
    /// process launched, not that it succeeded.
    @discardableResult
    static func runManaged(repoDir: URL, command: String) -> Bool {
        do {
            try managedProcess(repoDir: repoDir, command: command).run()
            return true
        } catch {
            return false
        }
    }

    /// Run a management subcommand and block until it finishes, returning
    /// whether it exited cleanly. Call off the main thread; used so the MCP
    /// "Connect" buttons reflect the real result instead of guessing.
    static func runManagedSync(repoDir: URL, command: String) -> Bool {
        let process = managedProcess(repoDir: repoDir, command: command)
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus == 0
        } catch {
            return false
        }
    }

    /// Run a management subcommand and capture its standard output, or nil if
    /// it failed to launch or exited non-zero. Call off the main thread.
    static func runManagedCapturing(repoDir: URL, command: String) -> String? {
        let process = managedProcess(repoDir: repoDir, command: command)
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = nil  // diagnostic logs land here; ignore them
        do {
            try process.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard process.terminationStatus == 0 else { return nil }
            return String(decoding: data, as: UTF8.self)
        } catch {
            return nil
        }
    }

    /// Health of the local Ollama dependency, as reported by `doctor --json`.
    struct OllamaHealth {
        let healthy: Bool
        let reachable: Bool
        let message: String
    }

    /// Probe the local model stack by running `second-brain doctor --json` and
    /// parsing its result. Returns ``nil`` only if the probe could not run.
    /// Call off the main thread; the probe can block briefly when Ollama is
    /// unreachable.
    static func checkOllama(repoDir: URL) -> OllamaHealth? {
        let process = managedProcess(repoDir: repoDir, command: "doctor --json")
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = nil  // diagnostic logs land here; ignore them
        do {
            try process.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            // uv or the shell may prepend lines; the JSON is the last {...} line.
            let line = String(decoding: data, as: UTF8.self)
                .split(separator: "\n")
                .last { $0.trimmingCharacters(in: .whitespaces).hasPrefix("{") }
            guard let jsonLine = line,
                  let obj = try? JSONSerialization.jsonObject(
                      with: Data(jsonLine.utf8)) as? [String: Any]
            else { return nil }
            return OllamaHealth(
                healthy: obj["healthy"] as? Bool ?? false,
                reachable: obj["reachable"] as? Bool ?? false,
                message: obj["message"] as? String ?? "Ollama status unknown."
            )
        } catch {
            return nil
        }
    }
}
