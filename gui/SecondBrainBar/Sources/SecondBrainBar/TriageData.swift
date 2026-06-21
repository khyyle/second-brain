import Foundation
import SQLite3

/// A triage decision for one ingested source.
struct TriageRow: Identifiable, Hashable {
    let id: String           // raw path
    let displayName: String
    let decision: Decision
    let confidence: Double
    let reason: String

    enum Decision: String {
        case worthwhile
        case review
        case skip

        var label: String {
            switch self {
            case .worthwhile: return "worthwhile"
            case .review:     return "review"
            case .skip:       return "skipped"
            }
        }
    }
}

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

/// Reads the append-only build log written by the compile stage.
enum BuildLog {
    // The on-disk log is capped (see build_log.py), so loading it whole is
    // cheap and lets a capped view show an accurate "+N more" count.
    static func recent(at url: URL, limit: Int = 500) -> [BuildLogEntry] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

        var entries: [BuildLogEntry] = []
        for line in text.split(separator: "\n").reversed() {
            guard let data = line.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let actionRaw = obj["action"] as? String,
                  let action = BuildLogEntry.Action(rawValue: actionRaw),
                  let path = obj["path"] as? String
            else { continue }
            let at = (obj["at"] as? String).flatMap { iso.date(from: $0) } ?? Date.distantPast
            entries.append(
                BuildLogEntry(
                    action: action,
                    pageName: (path as NSString).lastPathComponent,
                    relativePath: path,
                    at: at
                )
            )
            if entries.count >= limit { break }
        }
        return entries
    }
}
