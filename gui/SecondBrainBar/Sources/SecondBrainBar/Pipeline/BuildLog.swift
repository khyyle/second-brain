import Foundation

/// One entry in the wiki build log.
struct BuildLogEntry: Identifiable, Hashable {
    let id = UUID()
    let action: Action
    let pageName: String
    let relativePath: String
    let at: Date

    enum Action: String {
        case created
        case updated
    }
}

struct BuildActionResponse: Decodable {
    let action: String
    let path: String
    let at: String
    var entry: BuildLogEntry? {
        guard let kind = BuildLogEntry.Action(rawValue: action) else { return nil }
        return BuildLogEntry(action: kind, pageName: (path as NSString).lastPathComponent,
                             relativePath: path, at: backendDate(at) ?? .distantPast)
    }
}
