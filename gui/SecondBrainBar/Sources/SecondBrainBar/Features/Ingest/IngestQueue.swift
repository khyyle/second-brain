import Foundation

/// A staged file that has not finished ingestion yet.
struct QueueItem: Identifiable, Hashable {
    let id: String          // absolute path
    let displayName: String
    let state: State
    let since: Date         // when it entered its current state (for elapsed)

    enum State {
        case waiting        // in drops, not yet seen by the pipeline
        case processing     // currently being ingested
        case duplicate      // identical content already ingested elsewhere
        case failed         // ingestion failed

        var label: String {
            switch self {
            case .waiting:    return "waiting"
            case .processing: return "processing"
            case .duplicate:  return "already ingested"
            case .failed:     return "failed"
            }
        }
    }
}

/// Reads the ingest queue from the filesystem + manifest. Kept off the
/// SwiftUI types so views stay declarative.
enum IngestQueue {
    private static let supported: Set<String> = ["pdf", "md", "txt", "tex", "json"]

    /// Files staged in `drops/` that are not yet completed.
    static func queue(config: AppConfig) -> [QueueItem] {
        let statuses = ManifestReader(dbPath: config.manifestDB).statusByPath()
        let fm = FileManager.default
        guard let enumerator = fm.enumerator(
            at: config.dropsRoot,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles, .skipsPackageDescendants]
        ) else { return [] }

        var items: [(QueueItem, Date)] = []
        while let url = enumerator.nextObject() as? URL {
            guard supported.contains(url.pathExtension.lowercased()) else { continue }
            let info = statuses[url.path]
            if info?.status == .complete { continue }

            let state: QueueItem.State
            switch info?.status {
            case .processing: state = .processing
            case .duplicate:  state = .duplicate
            case .failed:     state = .failed
            default:          state = .waiting
            }
            let mod = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate ?? Date.distantPast
            // For a processing item, "since" is when it entered processing.
            let since = info?.updatedAt ?? mod
            items.append((
                QueueItem(id: url.path, displayName: url.lastPathComponent, state: state, since: since),
                mod
            ))
        }
        return items.sorted { $0.1 > $1.1 }.map(\.0)
    }
}
