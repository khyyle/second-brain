import Foundation

/// One submitted file or unpacked chat and its ingestion outcome.
struct QueueItem: Identifiable, Hashable {
    let id: String          // drop path or relative raw path
    let displayName: String
    let state: State
    let since: Date         // when it entered its current state (for elapsed)
    var outcome: Outcome? = nil

    enum Outcome: String {
        case staged
        case unpacked
        case kept
        case review = "needs review"
        case skipped
        case alreadyProcessed = "already processed"
    }

    enum State {
        case waiting        // in drops, not yet seen by the pipeline
        case processing     // currently being ingested
        case triaging       // an imported chat the local model is triaging
        case duplicate      // identical content already ingested elsewhere
        case failed         // ingestion failed
        case finished       // just done; shown briefly with where it went

        var label: String {
            switch self {
            case .waiting:    return "waiting"
            case .processing: return "processing"
            case .triaging:   return "triaging"
            case .duplicate:  return "already processed"
            case .failed:     return "failed"
            case .finished:   return "done"
            }
        }

        var isWorking: Bool { self == .processing || self == .triaging }
        var isFinished: Bool { self == .finished || self == .duplicate }
    }
}

/// Reads the ingest queue from the filesystem + manifest. Kept off the
/// SwiftUI types so views stay declarative.
enum IngestQueue {
    private static let supported: Set<String> = ["pdf", "md", "txt", "tex", "json"]

    static func queue(config: AppConfig, submitted: [QueueItem] = []) -> [QueueItem] {
        let statuses = ManifestReader(dbPath: config.manifestDB).statusByPath()
        let submittedByPath = Dictionary(submitted.map { ($0.id, $0) }, uniquingKeysWith: { _, last in last })
        let fm = FileManager.default
        let enumerator = fm.enumerator(
            at: config.dropsRoot,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles, .skipsPackageDescendants]
        )

        var items: [QueueItem] = []
        while let url = enumerator?.nextObject() as? URL {
            guard supported.contains(url.pathExtension.lowercased()) else { continue }
            // macOS exposes the same file through /var and /private/var.
            let sourcePath = statuses[url.path] != nil || submittedByPath[url.path] != nil
                ? url.path : url.resolvingSymlinksInPath().path
            let submission = submittedByPath[sourcePath]
            if submission?.state.isFinished == true { continue }
            var info = statuses[sourcePath]
            // A re-submitted path can still have the previous copy's verdict.
            if let previous = info, let submission, previous.updatedAt < submission.since {
                info = nil
            }
            if info?.status == .complete || info?.status == .duplicate { continue }

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
            let since = info?.updatedAt ?? submission?.since ?? mod
            items.append(
                QueueItem(id: sourcePath, displayName: url.lastPathComponent, state: state, since: since)
            )
        }
        let listedPaths = Set(items.map(\.id))
        for (path, status) in statuses where status.status == .failed && !listedPaths.contains(path) {
            if let submission = submittedByPath[path], status.updatedAt < submission.since { continue }
            items.append(QueueItem(
                id: path,
                displayName: (path as NSString).lastPathComponent,
                state: .failed,
                since: status.updatedAt
            ))
        }
        return items.sorted { $0.id < $1.id }
    }
}

/// The In progress list in a stable order. A row keeps the place it first
/// appeared in, and when it finishes it stays there with where it went for a
/// moment before it is removed, so nothing jumps around while files are handled.
struct IngestRows {
    private(set) var items: [QueueItem] = []
    private static let finishedVisibleFor: TimeInterval = 5

    mutating func add(_ submitted: [QueueItem]) {
        let paths = Set(submitted.map(\.id))
        items.removeAll { paths.contains($0.id) }
        items += submitted.map { item in
            guard item.state == .duplicate else { return item }
            return QueueItem(
                id: item.id, displayName: item.displayName, state: .duplicate, since: Date()
            )
        }
    }

    mutating func remove(_ path: String) {
        items.removeAll { $0.id == path }
    }

    /// A missing progress snapshot is not a completion; rows leave only after
    /// an explicit outcome, a failure, or a removal.
    mutating func update(
        live: [QueueItem],
        failedIDs: Set<String>,
        outcome: (QueueItem) -> QueueItem.Outcome?
    ) {
        let now = Date()
        let liveByID = Dictionary(live.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        let known = Set(items.map(\.id))
        var next: [QueueItem] = []
        for item in items {
            if item.state.isFinished {
                if now.timeIntervalSince(item.since) < Self.finishedVisibleFor {
                    next.append(item)
                }
            } else if let current = liveByID[item.id] {
                next.append(current)
            } else if !failedIDs.contains(item.id), let outcome = outcome(item) {
                next.append(QueueItem(
                    id: item.id,
                    displayName: item.displayName,
                    state: outcome == .alreadyProcessed ? .duplicate : .finished,
                    since: now,
                    outcome: outcome
                ))
            } else if !failedIDs.contains(item.id) {
                // Queue membership survives gaps between progress snapshots.
                next.append(item)
            }
        }
        next += live.filter { !known.contains($0.id) }
        items = next
    }
}
