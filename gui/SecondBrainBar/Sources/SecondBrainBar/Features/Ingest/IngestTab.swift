import SwiftUI

/// The parsing pipeline: imported chats waiting for a keep-or-skip call and
/// the ones skipped, files currently being ingested, and any that failed. A
/// file leaves this tab once parsed and appears as "staged" on the Build tab.
struct IngestTab: View {
    let config: AppConfig
    @State private var queue: [QueueItem] = []
    @State private var review: [TriageRow] = []
    @State private var skipped: [TriageRow] = []
    @State private var rows = IngestRows()
    @State private var loaded = false
    @EnvironmentObject private var store: PipelineStore
    @EnvironmentObject private var intake: DropIntake

    var body: some View {
        let active = rows.items
        let failed = queue.filter { $0.state == .failed }
        return VStack(spacing: 1) {
            if !review.isEmpty {
                SectionHeader(
                    title: "Needs review",
                    help: "A local model sorts imported chats so only substantial ones "
                        + "become wiki pages. Chats it isn't sure about wait here for "
                        + "you to keep or skip."
                )
                PaginatedList(items: review, expansionKey: "ingest.review") { row in
                    ReviewRow(
                        row: row,
                        onOpen: { openReview(row) },
                        onKeep: { keep(row) },
                        onSkip: { skip(row) }
                    )
                }
            }

            if store.skippedCount > 0 {
                SectionHeader(
                    title: "Skipped",
                    help: "Chats set aside by triage. Restore one to include it in a future build."
                )
                PaginatedList(items: skipped, expansionKey: "ingest.skipped") { row in
                    SkippedRow(
                        row: row,
                        onOpen: { openSkipped(row) },
                        onKeep: { unskip(row) }
                    )
                }
            }

            SectionHeader(title: "In progress")
            if active.isEmpty {
                EmptyListMessage(text: loaded ? "Nothing ingesting. Drop a file above." : nil)
            } else {
                PaginatedList(items: active, expansionKey: "ingest.active") { item in
                    Group {
                        if item.state.isFinished {
                            QueueRow(item: item, onOpen: {})
                        } else if isChat(item) {
                            QueueRow(item: item, onOpen: { openRaw(item.id) })
                        } else {
                            QueueRow(item: item, onOpen: { open(item.id) }, onRemove: { remove(item.id) })
                        }
                    }
                    .transition(.opacity)
                }
            }

            if !failed.isEmpty {
                SectionHeader(
                    title: "Failed",
                    help: "These files couldn't be parsed. Retry ingestion "
                        + "or remove files."
                )
                PaginatedList(items: failed, expansionKey: "ingest.failed") { item in
                    QueueRow(
                        item: item,
                        failureReason: store.ingestFailureReason(for: item.id),
                        onOpen: { open(item.id) },
                        onRemove: isDropped(item.id) ? { remove(item.id) } : nil,
                        onRetry: { retry(item.id) }
                    )
                }
            }
        }
        .onAppear {
            if let added = intake.added { rows.add(added.files) }
            refreshQueue()
            refreshReview()
            refreshSkipped()
        }
        .onPanelShow {
            refreshQueue()
            refreshReview()
            refreshSkipped()
        }
        .onReceive(Ticker.everySecond) { _ in
            refreshQueue()
        }
        .onChange(of: intake.added) { added in
            guard let added else { return }
            withAnimation(.easeOut(duration: 0.3)) { rows.add(added.files) }
            refreshQueue()
        }
        .onChange(of: store.stateStamp) { _ in
            refreshReview()
            refreshSkipped()
        }
    }

    /// Pending chats remain listed even when there is no active heartbeat.
    private var triageQueue: [QueueItem] {
        let progress = store.triageProgress
        let current = Set(progress?.currentSources ?? [])
        let pending = Set(store.pendingTriage + (progress?.currentSources ?? []) + (progress?.queuedSources ?? []))
        return pending.sorted().map { path in
            QueueItem(
                id: path,
                displayName: (path as NSString).lastPathComponent,
                state: current.contains(path) ? .triaging : .waiting,
                since: progress?.currentStartedAt ?? Date()
            )
        }
    }

    private func refreshQueue() {
        queue = IngestQueue.queue(imports: store.imports)
            .filter { !store.isRemoving($0.id) }
        withAnimation(.easeOut(duration: 0.3)) {
            rows.update(
                live: triageQueue + queue.filter { $0.state != .failed },
                failedIDs: Set(queue.filter { $0.state == .failed }.map(\.id)),
                outcome: { item in outcome(of: item) }
            )
        }
        loaded = true
    }

    /// A chat being triaged is keyed by its raw path; a dropped file by its
    /// absolute path in the drop folder.
    private func isChat(_ item: QueueItem) -> Bool { !item.id.hasPrefix("/") }

    private func isDropped(_ path: String) -> Bool {
        let drops = config.dropsRoot.resolvingSymlinksInPath().path
        return URL(fileURLWithPath: path).resolvingSymlinksInPath().path.hasPrefix(drops + "/")
    }

    /// Where an item went once it left the in-progress list.
    private func outcome(of item: QueueItem) -> QueueItem.Outcome? {
        if !isChat(item) {
            guard let status = store.imports.first(where: { $0.file_path == item.id }) else {
                return nil
            }
            switch status.status {
            case "complete":
                return URL(fileURLWithPath: item.id).pathExtension.lowercased() == "json" ? .unpacked : .staged
            case "duplicate": return .alreadyProcessed
            default: return nil
            }
        }
        switch store.triage.first(where: { $0.id == item.id })?.decision {
        case .worthwhile: return .kept
        case .review: return .review
        case .skip: return .skipped
        default: return nil
        }
    }

    private func refreshReview() { review = store.triage.filter { $0.decision == .review } }
    private func refreshSkipped() { skipped = store.triage.filter { $0.decision == .skip } }
    private func keep(_ row: TriageRow) { store.setTriage(row.id, decision: "worthwhile") }
    private func skip(_ row: TriageRow) { store.setTriage(row.id, decision: "skip") }
    private func unskip(_ row: TriageRow) { store.setTriage(row.id, decision: "worthwhile") }

    private func remove(_ path: String) {
        withAnimation(.easeOut(duration: 0.3)) {
            rows.remove(path)
            queue.removeAll { $0.id == path }
            store.removeDroppedSource(path)
        }
    }

    private func retry(_ path: String) {
        store.retryIngestion(path)
        refreshQueue()
    }

    private func open(_ path: String) {
        openInDefaultApp(URL(fileURLWithPath: path))
    }

    private func openRaw(_ rawRel: String) {
        openInDefaultApp(config.rawRoot.appending(path: rawRel))
    }

    private func openReview(_ row: TriageRow) {
        openInDefaultApp(config.rawRoot.appending(path: row.id))
    }

    private func openSkipped(_ row: TriageRow) {
        openInDefaultApp(config.rawRoot.appending(path: ".skipped/\(row.id)"))
    }
}
