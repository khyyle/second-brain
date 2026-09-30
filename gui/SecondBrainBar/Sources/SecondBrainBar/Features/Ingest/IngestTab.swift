import SwiftUI

/// The parsing pipeline: imported chats waiting for a keep-or-skip call,
/// files currently being ingested, and any that failed. A file leaves this
/// tab once parsed and appears as "staged" on the Build tab.
struct IngestTab: View {
    let config: AppConfig
    @State private var queue: [QueueItem] = []
    @State private var review: [TriageRow] = []
    @State private var loaded = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        let active = queue.filter { $0.state != .failed }
        let failed = queue.filter { $0.state == .failed }
        return VStack(spacing: 1) {
            if !review.isEmpty {
                SectionHeader(
                    title: "Needs review",
                    help: "A local model sorts imported chats so only substantial ones "
                        + "become wiki pages. Chats it isn't sure about wait here for "
                        + "you to keep or skip."
                )
                PaginatedList(items: review) { row in
                    ReviewRow(
                        row: row,
                        onOpen: { openReview(row) },
                        onKeep: { keep(row) },
                        onSkip: { skip(row) }
                    )
                }
            }

            SectionHeader(title: "In progress")
            if active.isEmpty {
                EmptyListMessage(text: loaded ? "Nothing ingesting. Drop a file above." : nil)
            } else {
                PaginatedList(items: active) { item in
                    QueueRow(item: item, onOpen: { open(item.id) }, onRemove: { remove(item.id) })
                }
            }

            if !failed.isEmpty {
                SectionHeader(
                    title: "Failed",
                    help: "These files couldn't be parsed. Retry ingestion "
                        + "or remove files."
                )
                PaginatedList(items: failed) { item in
                    QueueRow(
                        item: item,
                        onOpen: { open(item.id) },
                        onRemove: { remove(item.id) },
                        onRetry: { retry(item.id) }
                    )
                }
            }
        }
        .onAppear {
            refreshQueue()
            refreshReview()
        }
        .onPanelShow {
            refreshQueue()
            refreshReview()
        }
        .onReceive(Timer.publish(every: 1, on: .main, in: .common).autoconnect()) { _ in
            refreshQueue()
        }
        .onChange(of: store.stateStamp) { _ in refreshReview() }
    }

    private func refreshQueue() {
        queue = IngestQueue.queue(config: config)
        loaded = true
    }

    private func refreshReview() {
        review = ManifestReader(dbPath: config.manifestDB)
            .triageDecisions()
            .filter { row in
                row.decision == .review
                    && FileManager.default.fileExists(atPath: config.rawRoot.appending(path: row.id).path)
            }
    }

    private func keep(_ row: TriageRow) {
        ManifestMutator.setTriageDecision(config: config, rawPath: row.id, decision: "worthwhile")
        refreshReview()
    }

    private func skip(_ row: TriageRow) {
        ManifestMutator.skipSource(config: config, rawRel: row.id)
        refreshReview()
    }

    private func remove(_ path: String) {
        ManifestMutator.removeSource(config: config, filePath: path)
        refreshQueue()
    }

    private func retry(_ path: String) {
        ManifestMutator.retryIngest(config: config, filePath: path)
        refreshQueue()
    }

    private func open(_ path: String) {
        openInDefaultApp(URL(fileURLWithPath: path))
    }

    private func openReview(_ row: TriageRow) {
        openInDefaultApp(config.rawRoot.appending(path: row.id))
    }
}
