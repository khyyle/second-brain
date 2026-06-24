import Foundation

/// A wiki domain and how many pages currently declare it.
struct DomainInfo: Identifiable {
    let name: String
    let pageCount: Int
    let inSchema: Bool
    var id: String { name }
}

/// Reads the wiki's domain vocabulary via `second-brain domain list --json`.
///
/// Domains live in page frontmatter and the topic schema, which the Python
/// side already parses; shelling out keeps that the single source of truth
/// instead of re-parsing YAML here.
enum DomainData {
    /// Load all domains with page counts. Call off the main thread (this spawns
    /// a subprocess). Returns nil when the command can't run; an empty wiki
    /// yields an empty array.
    static func load(config: AppConfig) -> [DomainInfo]? {
        guard let repo = config.repoDir,
              let output = PipelineRunner.runManagedCapturing(
                  repoDir: repo, command: "domain list --json"
              )
        else { return nil }

        // uv or the shell may prepend lines; the JSON is the last [...] line.
        guard let jsonLine = output
            .split(separator: "\n")
            .last(where: { $0.trimmingCharacters(in: .whitespaces).hasPrefix("[") }),
            let parsed = try? JSONSerialization.jsonObject(with: Data(jsonLine.utf8)),
            let rows = parsed as? [[String: Any]]
        else { return nil }

        return rows.compactMap { row in
            guard let name = row["name"] as? String, !name.isEmpty else { return nil }
            return DomainInfo(
                name: name,
                pageCount: row["count"] as? Int ?? 0,
                inSchema: row["in_schema"] as? Bool ?? false
            )
        }
    }
}
