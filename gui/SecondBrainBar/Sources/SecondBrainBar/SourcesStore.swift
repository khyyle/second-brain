import Foundation

/// A watched source directory the pipeline scans during ingest, beyond the
/// built-in drop folders. Encoded as-is into `sources.json`, which the
/// Python `load_config` folds into its `sources` map.
struct WatchedFolder: Identifiable, Codable, Equatable {
    var name: String
    var path: String
    var enabled: Bool
    var file_types: [String]

    var id: String { name }
}

/// Reads and writes `<vault>/sources.json`, the GUI-owned list of watched
/// folders. Kept separate from config.yaml so the app never has to edit
/// nested YAML; the built-in drop folders stay in config.yaml untouched.
enum SourcesStore {
    static let defaultFileTypes = ["pdf", "md", "txt"]
    static let reservedNames: Set<String> = ["chatgpt", "documents"]

    private static func url(_ config: AppConfig) -> URL {
        config.vaultRoot.appending(path: "sources.json")
    }

    static func load(_ config: AppConfig) -> [WatchedFolder] {
        guard let data = try? Data(contentsOf: url(config)),
              let list = try? JSONDecoder().decode([WatchedFolder].self, from: data)
        else { return [] }
        return list
    }

    @discardableResult
    static func save(_ folders: [WatchedFolder], _ config: AppConfig) -> Bool {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(folders) else { return false }
        return (try? data.write(to: url(config), options: .atomic)) != nil
    }

    /// A filesystem-safe, unique source name derived from a folder URL,
    /// avoiding the reserved built-ins and any already-watched names.
    static func uniqueName(for folderURL: URL, existing: [WatchedFolder]) -> String {
        let base = folderURL.lastPathComponent
            .lowercased()
            .replacingOccurrences(of: " ", with: "-")
            .filter { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
        let stem = base.isEmpty ? "folder" : base

        let taken = reservedNames.union(existing.map(\.name))
        if !taken.contains(stem) { return stem }
        var n = 2
        while taken.contains("\(stem)-\(n)") { n += 1 }
        return "\(stem)-\(n)"
    }
}
