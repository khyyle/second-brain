import Foundation

/// The user-facing knobs surfaced in Settings. A subset of the pipeline's
/// `config.yaml`; everything else in that file is left untouched.
struct PipelineSettings: Equatable {
    var triageEnabled: Bool
    var triageProfile: String
    var scheduleHours: [Int]
    var maxCostPerBuildUSD: Double
    var provider: String
    var model: String

    static let profiles = ["balanced", "technical", "skip_heavy", "project_heavy", "lenient"]

    /// The selected compilation provider, defaulting to Anthropic if the
    /// config holds an unrecognized value.
    var llmProvider: LLMProvider { LLMProvider(rawValue: provider) ?? .anthropic }

    static let fallback = PipelineSettings(
        triageEnabled: true,
        triageProfile: "balanced",
        scheduleHours: [8, 14, 20],
        maxCostPerBuildUSD: 0,
        provider: "anthropic",
        model: "claude-sonnet-4-6"
    )
}

/// Reads and writes the handful of settings the GUI exposes directly in
/// the project's `config/config.yaml` -- the same file the Python pipeline
/// loads, so there is one source of truth and no daemon to restart. Writes
/// are surgical: only the targeted scalar line changes, leaving comments,
/// ordering, and every other key byte-for-byte intact.
enum ConfigStore {
    /// The config file, but only if it actually exists on disk.
    static func locate(_ config: AppConfig) -> URL? {
        guard let url = config.configFile,
              FileManager.default.fileExists(atPath: url.path) else { return nil }
        return url
    }

    static func load(from url: URL) -> PipelineSettings {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            return .fallback
        }
        let doc = YAMLScalars(text)
        let profile = doc.value(section: "triage", key: "profile") ?? "balanced"
        let costCap = Double(doc.value(section: "compilation", key: "max_cost_per_build_usd") ?? "")
        let provider = doc.value(section: "compilation", key: "provider") ?? "anthropic"
        let model = doc.value(section: "compilation", key: "model") ?? "claude-sonnet-4-6"
        return PipelineSettings(
            triageEnabled: doc.bool(section: "triage", key: "enabled", default: true),
            triageProfile: PipelineSettings.profiles.contains(profile) ? profile : "balanced",
            scheduleHours: parseHours(doc.value(section: "schedule", key: "hours") ?? ""),
            maxCostPerBuildUSD: max(0, costCap ?? 0),
            provider: provider,
            model: model
        )
    }

    @discardableResult
    static func save(_ s: PipelineSettings, to url: URL) -> Bool {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return false }
        var doc = YAMLScalars(text)
        doc.set(section: "triage", key: "enabled", value: s.triageEnabled ? "true" : "false")
        doc.set(section: "triage", key: "profile", value: s.triageProfile)
        doc.set(section: "schedule", key: "hours", value: formatHours(s.scheduleHours))
        doc.set(
            section: "compilation", key: "max_cost_per_build_usd",
            value: String(format: "%g", s.maxCostPerBuildUSD)
        )
        doc.set(section: "compilation", key: "provider", value: s.provider)
        doc.set(section: "compilation", key: "model", value: s.model)
        do {
            try doc.text.write(to: url, atomically: true, encoding: .utf8)
            return true
        } catch {
            return false
        }
    }

    static func parseHours(_ raw: String) -> [Int] {
        raw.trimmingCharacters(in: CharacterSet(charactersIn: "[] "))
            .split(separator: ",")
            .compactMap { Int($0.trimmingCharacters(in: .whitespaces)) }
            .filter { (0...23).contains($0) }
    }

    static func formatHours(_ hours: [Int]) -> String {
        "[" + hours.map(String.init).joined(separator: ", ") + "]"
    }
}

/// Minimal, section-aware editor for top-level `section:` blocks whose
/// children are plain `key: value` scalars. This is deliberately *not* a
/// general YAML parser: it only finds and rewrites the exact lines asked
/// for, so comments and untouched content survive a round trip verbatim.
private struct YAMLScalars {
    private var lines: [String]

    init(_ text: String) { lines = text.components(separatedBy: "\n") }

    var text: String { lines.joined(separator: "\n") }

    func value(section: String, key: String) -> String? {
        var inSection = false
        for line in lines {
            if isHeader(line) {
                inSection = headerName(line) == section
                continue
            }
            if inSection, let v = scalar(line, key: key) { return v }
        }
        return nil
    }

    func bool(section: String, key: String, default fallback: Bool) -> Bool {
        guard let v = value(section: section, key: key) else { return fallback }
        return v == "true"
    }

    mutating func set(section: String, key: String, value: String) {
        var inSection = false
        for i in lines.indices {
            if isHeader(lines[i]) {
                inSection = headerName(lines[i]) == section
                continue
            }
            if inSection, scalar(lines[i], key: key) != nil {
                lines[i] = rewrite(lines[i], key: key, value: value)
                return
            }
        }
    }

    /// A top-level map header: no indentation, ends with ':' and names no
    /// inline value (e.g. `triage:`), and isn't a comment.
    private func isHeader(_ line: String) -> Bool {
        guard let first = line.first, first != " ", first != "\t", first != "#" else { return false }
        let t = line.trimmingCharacters(in: .whitespaces)
        return t.hasSuffix(":") && !t.contains(" ")
    }

    private func headerName(_ line: String) -> String {
        String(line.trimmingCharacters(in: .whitespaces).dropLast())
    }

    /// If `line` is an indented `key: value`, return the value (comment
    /// stripped). The key must sit at the start of the line after its
    /// indentation, so `enabled` won't match `semantic_enabled`.
    private func scalar(_ line: String, key: String) -> String? {
        let indent = line.prefix { $0 == " " || $0 == "\t" }
        guard !indent.isEmpty else { return nil }
        let rest = line.dropFirst(indent.count)
        guard rest.hasPrefix("\(key):") else { return nil }
        var v = String(rest.dropFirst(key.count + 1))
        if let hash = v.firstIndex(of: "#") { v = String(v[..<hash]) }
        return v.trimmingCharacters(in: .whitespaces)
    }

    /// Rewrite a scalar line's value while preserving its indentation and
    /// any trailing inline comment.
    private func rewrite(_ line: String, key: String, value: String) -> String {
        let indent = String(line.prefix { $0 == " " || $0 == "\t" })
        let afterKey = line.dropFirst(indent.count + key.count + 1)
        var comment = ""
        if let hash = afterKey.firstIndex(of: "#") {
            comment = "   " + String(afterKey[hash...])
        }
        return "\(indent)\(key): \(value)\(comment)"
    }
}
