import Foundation

/// One flagged item within a category. `page` is the wiki page stem to open, or
/// nil when the item is not page-backed (a gap points at a page that does not
/// exist). `detail` is an optional note shown beside it, e.g. a gap's reference
/// count. `pair` is a second page stem for items that flag a pair of pages
/// (a possible duplicate), rendered as its own clickable line.
struct HealthItem: Identifiable {
    let id = UUID()
    let text: String
    let page: String?
    let detail: String?
    let pair: String?
}

/// A single check and the items it flagged. `section` places it under either
/// "improve" (growth opportunities) or "health" (defects). An empty `items`
/// means the check passed.
struct HealthCategory: Identifiable {
    let key: String
    let label: String
    let section: String
    let items: [HealthItem]
    var id: String { key }
    var count: Int { items.count }
}

/// The wiki's overview, as a list of checks split across sections.
struct WikiHealth {
    let healthy: Bool
    let categories: [HealthCategory]

    func categories(in section: String) -> [HealthCategory] {
        categories.filter { $0.section == section }
    }
}

enum HealthData {
    /// Run the health check. Call off the main thread (this spawns a
    /// subprocess).
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
                return HealthItem(
                    text: text,
                    page: item["page"] as? String,
                    detail: item["detail"] as? String,
                    pair: item["pair"] as? String
                )
            }
            return HealthCategory(
                key: raw["key"] as? String ?? UUID().uuidString,
                label: raw["label"] as? String ?? "",
                section: raw["section"] as? String ?? "health",
                items: items
            )
        }
        return WikiHealth(healthy: parsed["healthy"] as? Bool ?? false, categories: categories)
    }
}
