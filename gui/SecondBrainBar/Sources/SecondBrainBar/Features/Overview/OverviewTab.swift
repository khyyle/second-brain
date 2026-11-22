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
    @State private var errorMessage: String?
    @EnvironmentObject private var store: PipelineStore
    @State private var merging: PendingMerge?

    var body: some View {
        VStack(spacing: 1) {
            content
        }
        .alert("Wiki action failed", isPresented: Binding(
            get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } }
        )) { Button("OK", role: .cancel) {} } message: { Text(errorMessage ?? "") }
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
            EmptyListMessage(text: "Unable to load the overview from the backend.")
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

    private func open(_ stem: String) {
        Task {
            do {
                let pages: [WikiPageResponse] = try await store.api.request("/wiki/pages")
                if let page = pages.first(where: { URL(fileURLWithPath: $0.path).deletingPathExtension().lastPathComponent == stem }) {
                    openInDefaultApp(config.wikiRoot.appending(path: page.path))
                }
            } catch { errorMessage = error.localizedDescription }
        }
    }
    private func dismiss(_ item: HealthItem) {
        guard let page = item.page, let pair = item.pair else { return }
        mutate("/wiki/dismissed-duplicates", body: ["page_a": page, "page_b": pair])
    }
    private func performMerge(dest: String, source: String) {
        merging = nil
        mutate("/wiki/merge", body: ["dest": dest, "sources": [source]])
    }
    private func mutate(_ path: String, body: [String: Any]) {
        Task {
            do { try await store.api.send(path, method: "POST", body: body); refresh() }
            catch { errorMessage = error.localizedDescription }
        }
    }
    private func refresh() {
        Task {
            do {
                let response: WikiHealthResponse = try await store.api.request("/wiki/health")
                health = response.presentation
                unavailable = false
            } catch { unavailable = true; errorMessage = error.localizedDescription }
            loaded = true
        }
    }
}
