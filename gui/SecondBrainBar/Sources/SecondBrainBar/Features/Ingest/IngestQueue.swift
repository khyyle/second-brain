import Foundation

enum IngestQueue {
    static func queue(imports: [ImportFile]) -> [QueueItem] {
        imports.filter { $0.status != "complete" && $0.status != "duplicate" }.map(\.queueItem)
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
