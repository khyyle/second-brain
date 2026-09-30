import SwiftUI

/// The parsing pipeline: files currently being ingested, plus any that
/// failed. A file leaves this tab once parsed and appears as "staged" on
/// the Build tab.
struct IngestTab: View {
    let config: AppConfig
    @State private var queue: [QueueItem] = []
    @State private var loaded = false

    var body: some View {
        let active = queue.filter { $0.state != .failed }
        let failed = queue.filter { $0.state == .failed }
        return VStack(spacing: 1) {
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
        .onAppear(perform: refresh)
        .onPanelShow(refresh)
        .onReceive(Timer.publish(every: 1, on: .main, in: .common).autoconnect()) { _ in refresh() }
    }

    private func refresh() {
        queue = IngestQueue.queue(config: config)
        loaded = true
    }

    private func remove(_ path: String) {
        ManifestMutator.removeSource(config: config, filePath: path)
        refresh()
    }

    private func retry(_ path: String) {
        ManifestMutator.retryIngest(config: config, filePath: path)
        refresh()
    }

    private func open(_ path: String) {
        openInDefaultApp(URL(fileURLWithPath: path))
    }
}
