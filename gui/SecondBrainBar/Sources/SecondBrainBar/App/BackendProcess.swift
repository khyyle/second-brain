import Foundation

/// Connects to a development server, or owns one backend process for this app's lifetime.
/// An externally started backend is never stopped when the GUI quits.
@MainActor
final class BackendProcess {
    private var process: Process?
    private var output: FileHandle?

    func connectOrStart(config: AppConfig) async throws {
        let client = BackendClient(config: config)
        if (try? await client.send("/jobs", query: ["limit": "1"])) != nil { return }
        guard let script = config.backendScript else {
            throw BackendError(message: "Start the backend with make backend, or run make dev to launch both.")
        }
        var isReady = false
        defer { if !isReady { stop() } }
        let child = Process()
        child.executableURL = URL(fileURLWithPath: "/bin/bash")
        child.arguments = [script.path, "--no-reload"]
        child.currentDirectoryURL = script.deletingLastPathComponent()
        var env = ProcessInfo.processInfo.environment
        env["SECOND_BRAIN_VAULT"] = config.vaultRoot.path
        env["SECOND_BRAIN_PORT"] = String(config.port)
        env["PYTHON_DOTENV_DISABLED"] = "1"
        env["PATH"] = "\(FileManager.default.homeDirectoryForCurrentUser.path)/.local/bin:/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        child.environment = env
        // Keep bootstrap diagnostics outside the vault until the backend creates it.
        let logs = FileManager.default.temporaryDirectory.appending(path: "second-brain-backend-\(UUID().uuidString).log")
        FileManager.default.createFile(atPath: logs.path, contents: nil, attributes: [.posixPermissions: 0o600])
        output = try FileHandle(forWritingTo: logs)
        child.standardOutput = output
        child.standardError = output
        try child.run()
        process = child
        for _ in 0..<150 {
            if (try? await client.send("/jobs", query: ["limit": "1"])) != nil {
                isReady = true
                return
            }
            if !child.isRunning { break }
            try await Task.sleep(nanoseconds: 200_000_000)
        }
        throw BackendError(message: "The backend did not become ready. See \(logs.path).")
    }

    func stop() {
        if process?.isRunning == true { process?.terminate() }
        process = nil
        try? output?.close()
        output = nil
    }
}
