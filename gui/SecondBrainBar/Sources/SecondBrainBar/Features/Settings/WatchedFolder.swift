import Foundation

/// Watched folder configuration returned by the backend.
struct WatchedFolder: Identifiable, Codable, Equatable {
    var name: String
    var path: String
    var enabled: Bool
    var file_types: [String]
    var force_parse_lane: String? = nil

    var id: String { name }
}

/// Naming helpers for a new folder; persistence uses /settings.
enum WatchedFolderNames {
    static let reservedNames: Set<String> = ["chatgpt", "documents"]

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
