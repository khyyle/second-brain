import Foundation

/// A conversation-export source the app can import, such as ChatGPT.
///
/// Each provider validates its own export by content (so a rename can't
/// fool it) and names the drop lane its files are copied into. Supporting a
/// new provider is a new case here plus a matching parser in the Python
/// pipeline; their export formats differ (ChatGPT uses a `mapping` node
/// tree, Claude a flat `chat_messages` array), so detection and parsing are
/// always per-provider.
enum ExportProvider: String, CaseIterable, Identifiable {
    case chatgpt

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .chatgpt: return "ChatGPT"
        }
    }

    /// Subfolder of `drops/` this provider's files are copied into; matches
    /// a `sources` key in `config.yaml`.
    var lane: String {
        switch self {
        case .chatgpt: return "chatgpt"
        }
    }

    /// Whether a single JSON file is this provider's export.
    func matches(_ url: URL) -> Bool {
        guard url.pathExtension.lowercased() == "json",
              let data = try? Data(contentsOf: url),
              let top = try? JSONSerialization.jsonObject(with: data)
        else { return false }
        switch self {
        case .chatgpt:
            guard let conversations = top as? [[String: Any]],
                  let first = conversations.first else { return false }
            return first["mapping"] is [String: Any]
        }
    }

    /// The export files to import from a user's selection: a matching file
    /// directly, or the matching `conversations*.json` shards inside a
    /// selected export folder (ignoring the bundled attachments/metadata).
    func exportFiles(in selection: [URL]) -> [URL] {
        let fileManager = FileManager.default
        var found: [URL] = []
        for url in selection {
            var isDirectory: ObjCBool = false
            guard fileManager.fileExists(atPath: url.path, isDirectory: &isDirectory) else { continue }
            if isDirectory.boolValue {
                let entries = (try? fileManager.contentsOfDirectory(
                    at: url, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles]
                )) ?? []
                found += entries.filter {
                    $0.lastPathComponent.lowercased().hasPrefix("conversations") && matches($0)
                }
            } else if matches(url) {
                found.append(url)
            }
        }
        return found
    }

    /// The first provider that recognizes any file in a selection, used to
    /// catch an export mistakenly dropped on the documents zone.
    static func detect(in selection: [URL]) -> ExportProvider? {
        allCases.first { !$0.exportFiles(in: selection).isEmpty }
    }
}
