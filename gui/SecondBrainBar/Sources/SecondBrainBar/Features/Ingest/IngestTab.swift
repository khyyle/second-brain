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
                    help: "Chats triage deemed as unworthy of compilation. Skipped chats can be restored until the next build."
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
                        onRemove: { remove(item.id) },
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
        queue = IngestQueue.queue(config: config, submitted: rows.items)
            .filter { !store.isRemoving($0.id) }
        let reader = ManifestReader(dbPath: config.manifestDB)
        withAnimation(.easeOut(duration: 0.3)) {
            rows.update(
                live: triageQueue + queue.filter { $0.state != .failed },
                failedIDs: Set(queue.filter { $0.state == .failed }.map(\.id)),
                outcome: { item in outcome(of: item, reader: reader) }
            )
        }
        loaded = true
    }

    /// A chat being triaged is keyed by its raw path; a dropped file by its
    /// absolute path in the drop folder.
    private func isChat(_ item: QueueItem) -> Bool { !item.id.hasPrefix("/") }

    /// Where an item went once it left the in-progress list.
    private func outcome(of item: QueueItem, reader: ManifestReader) -> QueueItem.Outcome? {
        if !isChat(item) {
            guard let status = reader.statusByPath()[item.id], status.updatedAt >= item.since else {
                return nil
            }
            switch status.status {
            case .complete:
                return URL(fileURLWithPath: item.id).pathExtension.lowercased() == "json" ? .unpacked : .staged
            case .duplicate: return .alreadyProcessed
            default: return nil
            }
        }
        switch reader.triageDecisions().first(where: { $0.id == item.id })?.decision {
        case .worthwhile: return .kept
        case .review: return .review
        case .skip: return .skipped
        default: return nil
        }
    }

    private func refreshReview() {
        review = ManifestReader(dbPath: config.manifestDB)
            .triageDecisions()
            .filter { row in
                row.decision == .review
                    && FileManager.default.fileExists(atPath: config.rawRoot.appending(path: row.id).path)
            }
    }

    private func refreshSkipped() {
        skipped = ManifestReader(dbPath: config.manifestDB)
            .triageDecisions()
            .filter { row in
                row.decision == .skip
                    && FileManager.default.fileExists(
                        atPath: ManifestMutator.skippedURL(config, row.id).path
                    )
            }
    }

    private func keep(_ row: TriageRow) {
        ManifestMutator.setTriageDecision(config: config, rawPath: row.id, decision: "worthwhile")
        refreshReview()
    }

    private func skip(_ row: TriageRow) {
        ManifestMutator.skipSource(config: config, rawRel: row.id)
        refreshReview()
        refreshSkipped()
    }

    private func unskip(_ row: TriageRow) {
        ManifestMutator.unskipSource(config: config, rawRel: row.id)
        refreshSkipped()
    }

    private func remove(_ path: String) {
        withAnimation(.easeOut(duration: 0.3)) {
            rows.remove(path)
            queue.removeAll { $0.id == path }
            store.removeDroppedSource(path)
        }
    }

    private func retry(_ path: String) {
        ManifestMutator.retryIngest(config: config, filePath: path)
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
        openInDefaultApp(ManifestMutator.skippedURL(config, row.id))
    }
}
