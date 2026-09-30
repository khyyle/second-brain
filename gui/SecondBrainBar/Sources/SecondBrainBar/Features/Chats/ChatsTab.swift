import SwiftUI

/// The chat-history curation lane: conversations needing a review
/// decision, with recent decisions below.
struct ChatsTab: View {
    let config: AppConfig
    @State private var rows: [TriageRow] = []
    @State private var loaded = false

    var body: some View {
        let review = rows.filter { $0.decision == .review }
        let decided = rows.filter { $0.decision != .review }

        return VStack(spacing: 1) {
            SectionHeader(
                title: "Needs review",
                help: "A local model sorts imported chats so only substantial ones "
                    + "become wiki pages. Chats it isn't sure about wait here for "
                    + "you to keep or skip."
            )
            if review.isEmpty {
                EmptyListMessage(text: loaded ? "Nothing needs review." : nil)
            } else {
                PaginatedList(items: review) { row in
                    ReviewRow(
                        row: row,
                        onOpen: { open(row) },
                        onKeep: { keep(row) },
                        onSkip: { skip(row) }
                    )
                }
            }

            SectionHeader(title: "Recent")
            if decided.isEmpty {
                EmptyListMessage(text: loaded ? "No triage decisions yet." : nil)
            } else {
                PaginatedList(items: decided) { row in
                    TriageDecidedRow(
                        row: row,
                        onOpen: { open(row) },
                        onSkip: { skip(row) },
                        onUnskip: { unskip(row) }
                    )
                }
            }
        }
        .onAppear(perform: refresh)
        .onPanelShow(refresh)
        .onReceive(Timer.publish(every: 3, on: .main, in: .common).autoconnect()) { _ in refresh() }
    }

    private func refresh() {
        // This is the chat-lane curation surface; only chat sources are
        // model-triaged, so decisions from other lanes never belong here.
        rows = ManifestReader(dbPath: config.manifestDB)
            .triageDecisions()
            .filter { $0.id.hasPrefix("chatgpt/") }
        loaded = true
    }

    private func keep(_ row: TriageRow) {
        ManifestMutator.setTriageDecision(config: config, rawPath: row.id, decision: "worthwhile")
        refresh()
    }

    private func skip(_ row: TriageRow) {
        ManifestMutator.skipSource(config: config, rawRel: row.id)
        refresh()
    }

    private func unskip(_ row: TriageRow) {
        ManifestMutator.unskipSource(config: config, rawRel: row.id)
        refresh()
    }

    private func open(_ row: TriageRow) {
        // A skip can come from the app (file moved to the hidden .skipped/
        // holding folder) or from the triage pipeline (file left in raw/), so
        // open whichever location actually has it.
        let raw = config.rawRoot.appending(path: row.id)
        let base: URL
        if row.decision == .skip {
            let skipped = ManifestMutator.skippedURL(config, row.id)
            base = FileManager.default.fileExists(atPath: skipped.path) ? skipped : raw
        } else {
            base = raw
        }
        openInDefaultApp(base)
    }
}
