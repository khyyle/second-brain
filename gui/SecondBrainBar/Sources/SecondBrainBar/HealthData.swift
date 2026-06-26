import Foundation

/// One flagged item within a health category. `page` is the wiki page stem to
/// open, or nil when the item is not page-backed (a broken link points at a
/// page that does not exist).
struct HealthItem: Identifiable {
    let id = UUID()
    let text: String
    let page: String?
}

/// A single health check and the items it flagged. An empty `items` means the
/// check passed.
struct HealthCategory: Identifiable {
    let key: String
    let label: String
    let items: [HealthItem]
    var id: String { key }
    var count: Int { items.count }
}

/// The wiki's structural health, as a list of checks.
struct WikiHealth {
    let healthy: Bool
    let categories: [HealthCategory]
}

/// Reads wiki health via `second-brain health --json`, so the link-graph and
/// staleness checks stay defined once on the Python side instead of being
/// re-implemented here.
enum HealthData {
    /// Run the health check. Call off the main thread (this spawns a
    /// subprocess). Returns nil when the command can't run.
    static func load(config: AppConfig) -> WikiHealth? {
        guard let repo = config.repoDir,
              let output = PipelineRunner.runManagedCapturing(
                  repoDir: repo, command: "health --json"
              )
        else { return nil }

        // uv or the shell may prepend lines; the JSON is the last {...} line.
        guard let jsonLine = output
            .split(separator: "\n")
            .last(where: { $0.trimmingCharacters(in: .whitespaces).hasPrefix("{") }),
            let parsed = try? JSONSerialization.jsonObject(
                with: Data(jsonLine.utf8)) as? [String: Any]
        else { return nil }

        let categories = (parsed["categories"] as? [[String: Any]] ?? []).map { raw -> HealthCategory in
            let items = (raw["items"] as? [[String: Any]] ?? []).compactMap { item -> HealthItem? in
                guard let text = item["text"] as? String else { return nil }
                return HealthItem(text: text, page: item["page"] as? String)
            }
            return HealthCategory(
                key: raw["key"] as? String ?? UUID().uuidString,
                label: raw["label"] as? String ?? "",
                items: items
            )
        }
        return WikiHealth(healthy: parsed["healthy"] as? Bool ?? false, categories: categories)
    }
}
