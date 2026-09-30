import SwiftUI

/// A duplicate pair the user chose to merge
private struct PendingMerge {
    let pageA: String
    let pageB: String
}

/// The compiled wiki's overview: growth opportunities to improve it, then its
/// structural health, both from `second-brain health`.
struct OverviewTab: View {
    let config: AppConfig
    @State private var health: WikiHealth?
    @State private var loaded = false
    @State private var unavailable = false
    @State private var merging: PendingMerge?

    var body: some View {
        VStack(spacing: 1) {
            content
        }
        .onAppear(perform: refresh)
        .onPanelShow(refresh)
        .alert("Merge duplicates", isPresented: mergePresented) {
            if let merge = merging {
                Button("Keep '\(merge.pageA)'", role: .destructive) {
                    performMerge(dest: merge.pageA, source: merge.pageB)
                }
                Button("Keep '\(merge.pageB)'", role: .destructive) {
                    performMerge(dest: merge.pageB, source: merge.pageA)
                }
            }
            Button("Cancel", role: .cancel) { merging = nil }
        } message: {
            Text(
                "The kept page will absorb the other's links and sources. The retired page "
                    + "will be deleted without copying its text over."
            )
        }
    }

    private var mergePresented: Binding<Bool> {
        Binding(get: { merging != nil }, set: { if !$0 { merging = nil } })
    }

    @ViewBuilder
    private var content: some View {
        if !loaded {
            EmptyListMessage(text: nil)
        } else if unavailable {
            EmptyListMessage(text: "The overview needs the installed pipeline. Reinstall to view it.")
        } else if let health {
            section(title: "Improve your wiki", categories: health.categories(in: "improve"))
            section(title: "Health", categories: health.categories(in: "health"))
        }
    }

    /// One titled section over its checks. Rendered only when the section has
    /// checks to show.
    @ViewBuilder
    private func section(title: String, categories: [HealthCategory]) -> some View {
        if !categories.isEmpty {
            SectionHeader(title: title)
            ForEach(categories) { category in
                let actionable = category.key == "possible_duplicates"
                HealthCategoryRow(
                    category: category,
                    onOpen: open,
                    onDismiss: actionable ? dismiss : nil,
                    onMerge: actionable ? { merging = PendingMerge(pageA: $0, pageB: $1) } : nil
                )
            }
        }
    }

    /// Open a flagged page by stem; the flat wiki keeps stems unique, so the
    /// first content folder that has it wins.
    private func open(_ stem: String) {
        for dir in ["concepts", "problems", "projects", "papers", "insights"] {
            let url = config.wikiRoot.appending(path: "\(dir)/\(stem).md")
            if FileManager.default.fileExists(atPath: url.path) {
                openInDefaultApp(url)
                return
            }
        }
    }

    /// Record that a suggested pair is not a duplicate, then reload so the
    /// next closest pair takes its slot.
    private func dismiss(_ item: HealthItem) {
        guard let page = item.page, let pair = item.pair else { return }
        runWikiCommand("wiki dismiss \(page) \(pair)")
    }

    /// Apply the confirmed merge
    private func performMerge(dest: String, source: String) {
        merging = nil
        runWikiCommand("wiki merge \(dest) \(source)")
    }

    private func runWikiCommand(_ command: String) {
        guard let repo = config.repoDir else { return }
        DispatchQueue.global(qos: .userInitiated).async {
            _ = PipelineRunner.runManagedSync(repoDir: repo, command: command)
            DispatchQueue.main.async { refresh() }
        }
    }

    private func refresh() {
        guard config.repoDir != nil else {
            unavailable = true
            loaded = true
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let result = HealthData.load(config: config)
            DispatchQueue.main.async {
                if let result {
                    health = result
                    unavailable = false
                } else {
                    unavailable = true
                }
                loaded = true
            }
        }
    }
}
