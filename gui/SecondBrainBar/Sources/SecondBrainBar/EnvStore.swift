import Foundation

/// Reads and writes the Anthropic API key in the project's `.env` -- the
/// same file `run.sh` sources before every pipeline run (GUI-launched,
/// scheduled, or CLI), so a key set here reaches all of them. The file is
/// written owner-only (chmod 600) and is gitignored.
enum EnvStore {
    static let keyName = "ANTHROPIC_API_KEY"

    static func locate(_ config: AppConfig) -> URL? {
        config.repoDir?.appending(path: ".env")
    }

    /// The currently stored key, or "" if none / not locatable.
    static func readKey(_ config: AppConfig) -> String {
        guard let url = locate(config),
              let text = try? String(contentsOf: url, encoding: .utf8) else { return "" }
        for line in text.components(separatedBy: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard trimmed.hasPrefix("\(keyName)=") else { continue }
            return unquote(String(trimmed.dropFirst(keyName.count + 1)))
        }
        return ""
    }

    /// Set (or replace) the key line, preserving any other `.env` entries,
    /// and lock the file to owner-only.
    @discardableResult
    static func writeKey(_ value: String, _ config: AppConfig) -> Bool {
        guard let url = locate(config) else { return false }
        var lines = (try? String(contentsOf: url, encoding: .utf8))?
            .components(separatedBy: "\n") ?? []

        // Write unquoted (Anthropic keys have no shell-special chars); the
        // reader still tolerates quoted values if a user added them.
        let newLine = "\(keyName)=\(value)"
        if let i = lines.firstIndex(where: {
            $0.trimmingCharacters(in: .whitespaces).hasPrefix("\(keyName)=")
        }) {
            lines[i] = newLine
        } else if lines.last == "" {
            lines[lines.count - 1] = newLine
            lines.append("")
        } else {
            lines.append(newLine)
        }

        do {
            try lines.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
            try? FileManager.default.setAttributes(
                [.posixPermissions: 0o600], ofItemAtPath: url.path
            )
            return true
        } catch {
            return false
        }
    }

    private static func unquote(_ raw: String) -> String {
        let v = raw.trimmingCharacters(in: .whitespaces)
        if v.count >= 2,
           (v.hasPrefix("\"") && v.hasSuffix("\"")) || (v.hasPrefix("'") && v.hasSuffix("'")) {
            return String(v.dropFirst().dropLast())
        }
        return v
    }
}
