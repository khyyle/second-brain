import SwiftUI

/// Sources staged for compilation, in the order the build takes them — grouped
/// into the build plan when a cluster preview is active — then sources a build
/// set aside, and a log of pages already built.
struct BuildTab: View {
    let config: AppConfig
    let onBuild: () -> Void
    let canBuild: Bool
    @State private var plan: ClusterPlan?
    @State private var overrides = ClusterOverrides.empty
    @State private var entries: [BuildLogEntry] = []
    @State private var loaded = false
    @State private var savingOverrides = false
    @State private var compilationModel = "claude-sonnet-4-6"
    @State private var groupingError: String?
    @EnvironmentObject private var store: PipelineStore

    // The reviewed plan drives the view while it still covers staging; the
    // pipeline's stale flag (via the store) is the authority on that.
    private var activePlan: ClusterPlan? {
        guard let plan, !store.stale else { return nil }
        return plan
    }

    // The grouped view stays visible (read-only) while a compile runs.
    private var displayPlan: ClusterPlan? {
        store.isCompiling ? plan : activePlan
    }

    var body: some View {
        VStack(spacing: 1) {
            StagedHeader(
                cost: store.cost(for: compilationModel),
                sourceCount: store.stagedForBuild.count,
                stale: store.stale,
                hasPlan: plan != nil,
                canPreview: store.connected
                    && store.stagedForBuild.filter { isConversation($0.id) }.count >= 2,
                onPreview: previewGrouping,
                onBuild: onBuild,
                canBuild: canBuild
            )
            stagedSection
            setAsideSection
            SectionHeader(title: "Recent")
            recentSection
        }
        .onAppear(perform: refresh)
        .onPanelShow(refresh)
        .onChange(of: store.stateStamp) { _ in refresh() }
        .disabled(savingOverrides)
        .alert(
            "Grouping didn't finish",
            isPresented: Binding(
                get: { groupingError != nil },
                set: { if !$0 { groupingError = nil } }
            )
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(groupingError ?? "")
        }
    }

    // Rows stay in build order and leave as each group finishes, so the one
    // being built is always first.
    @ViewBuilder
    private var stagedSection: some View {
        let stagedPaths = Set(store.stagedForBuild.map(\.id))
        if store.stagedForBuild.isEmpty {
            EmptyListMessage(
                text: store.hasState
                    ? "Nothing staged. Files you drop appear here once they're ingested."
                    : nil
            )
        } else if let plan = displayPlan {
            PaginatedList(
                items: plan.groups.compactMap { $0.narrowed(to: stagedPaths) },
                expansionKey: "build.staged"
            ) { group in
                ClusterGroupRow(
                    group: group,
                    model: compilationModel,
                    overrides: $overrides,
                    onCommit: writeOverrides,
                    onRemove: remove,
                    onOpen: openRaw
                )
            }
        } else {
            let building = store.buildingSources
            PaginatedList(items: store.stagedForBuild, expansionKey: "build.staged") { source in
                StagedRow(
                    name: source.displayName,
                    costText: formatEstimate(source.cost(for: compilationModel)),
                    isChat: isConversation(source.id),
                    onSkip: isConversation(source.id) ? { skip(source.id) } : nil,
                    isBuilding: building.contains(source.id),
                    onOpen: { openRaw(source.id) },
                    onRemove: { remove(source.id) }
                )
            }
        }
    }

    @ViewBuilder
    private var setAsideSection: some View {
        if !store.setAside.isEmpty {
            SectionHeader(
                title: "Set aside",
                help: "Builds couldn't finish these and skip them until you retry. "
                    + "Click \"Set aside\" on a row to see why."
            )
            PaginatedList(items: store.setAside, expansionKey: "build.setAside") { source in
                StagedRow(
                    name: source.displayName,
                    costText: formatEstimate(source.cost(for: compilationModel)),
                    deferReason: source.deferReason,
                    onRetry: source.canRetry ? { retry(source.id) } : nil,
                    isChat: isConversation(source.id),
                    onOpen: { openRaw(source.id) },
                    onRemove: { remove(source.id) }
                )
            }
        }
    }

    @ViewBuilder
    private var recentSection: some View {
        if entries.isEmpty {
            EmptyListMessage(text: loaded ? "Nothing built yet." : nil)
        } else {
            PaginatedList(items: entries, expansionKey: "build.recent") { entry in
                BuildRow(entry: entry, onOpen: { open(entry) })
            }
        }
    }

    private func refresh() {
        guard !savingOverrides else { return }
        plan = store.groupingPlan
        overrides = plan?.overrides ?? .empty
        entries = store.buildActions
        compilationModel = store.compilationModel
        loaded = store.hasState
    }

    private func isConversation(_ rel: String) -> Bool {
        ExportProvider.allCases.contains { rel.hasPrefix($0.lane + "/") }
    }

    private func previewGrouping() { store.previewGrouping() }

    private func writeOverrides() {
        savingOverrides = true
        let submitted = overrides
        Task {
            defer { savingOverrides = false; refresh() }
            do {
                overrides = try await store.api.request("/grouping-plan/overrides", method: "PUT",
                    body: ["excluded": submitted.excluded, "split_groups": submitted.splitGroups])
                await store.refreshNow()
            } catch {
                overrides = plan?.overrides ?? .empty
                groupingError = error.localizedDescription
            }
        }
    }
    private func remove(_ path: String) { store.removeStagedSource(path) }
    private func skip(_ path: String) { store.setTriage(path, decision: "skip") }
    private func retry(_ path: String) { store.recompile(path) }

    private func openRaw(_ rawRel: String) {
        openInDefaultApp(config.rawRoot.appending(path: rawRel))
    }

    private func open(_ entry: BuildLogEntry) {
        openInDefaultApp(config.wikiRoot.appending(path: entry.relativePath))
    }
}
