import Foundation

/// A conversation export recognized in a drop or picker selection.
struct DetectedExport {
    let provider: ExportProvider
    let files: [URL]
    let conversationCount: Int

    /// The file name, or a count when a split export spans several files.
    var sourceName: String {
        files.count == 1
            ? files[0].lastPathComponent
            : "\(files.count) files"
    }
}

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

    /// Conversation count when a single JSON file is this provider's export;
    /// nil otherwise.
    func conversationCount(in url: URL) -> Int? {
        guard url.pathExtension.lowercased() == "json",
              let data = try? Data(contentsOf: url),
              let top = try? JSONSerialization.jsonObject(with: data)
        else { return nil }
        switch self {
        case .chatgpt:
            guard let conversations = top as? [[String: Any]],
                  let first = conversations.first,
                  first["mapping"] is [String: Any] else { return nil }
            return conversations.count
        }
    }

    /// The export to import from a user's selection: a matching file
    /// directly, or the matching `conversations*.json` shards inside a
    /// selected export folder (ignoring the bundled attachments/metadata).
    func detect(in selection: [URL]) -> DetectedExport? {
        let fileManager = FileManager.default
        var found: [URL] = []
        var totalConversations = 0
        for url in selection {
            var isDirectory: ObjCBool = false
            guard fileManager.fileExists(atPath: url.path, isDirectory: &isDirectory) else { continue }
            if isDirectory.boolValue {
                let entries = (try? fileManager.contentsOfDirectory(
                    at: url, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles]
                )) ?? []
                for entry in entries {
                    guard entry.lastPathComponent.lowercased().hasPrefix("conversations"),
                          let count = conversationCount(in: entry) else { continue }
                    found.append(entry)
                    totalConversations += count
                }
            } else if let count = conversationCount(in: url) {
                found.append(url)
                totalConversations += count
            }
        }
        guard !found.isEmpty else { return nil }
        return DetectedExport(provider: self, files: found, conversationCount: totalConversations)
    }

    /// The first provider that recognizes an export in the selection.
    static func detect(in selection: [URL]) -> DetectedExport? {
        for provider in allCases {
            if let detected = provider.detect(in: selection) { return detected }
        }
        return nil
    }
}
