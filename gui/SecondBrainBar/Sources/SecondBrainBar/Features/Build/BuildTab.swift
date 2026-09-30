import SwiftUI

/// Sources staged for compilation — grouped into the build plan when a
/// cluster preview is active — plus a log of pages already built.
struct BuildTab: View {
    let config: AppConfig
    let onBuild: () -> Void
    let canBuild: Bool
    @State private var plan: ClusterPlan?
    @State private var planStamp = ""
    @State private var overrides = ClusterOverrides.empty
    @State private var showAllClusters = false
    @State private var showSingletons = false
    @State private var entries: [BuildLogEntry] = []
    @State private var loaded = false
    @State private var compilationModel = "claude-sonnet-4-6"
    @EnvironmentObject private var store: PipelineStore

    // The reviewed plan drives the view while it still matches staging; the
    // pipeline's stale flag (via the store) is the authority on that match.
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
                sourceCount: store.staged.count,
                stale: store.stale,
                hasPlan: plan != nil,
                canPreview: config.repoDir != nil
                    && store.staged.contains { $0.id.hasPrefix("chatgpt/") },
                onPreview: previewGrouping,
                onBuild: onBuild,
                canBuild: canBuild
            )
            stagedSection
            SectionHeader(title: "Recent")
            recentSection
        }
        .onAppear(perform: refresh)
        .onPanelShow(refresh)
        .onReceive(Timer.publish(every: 1, on: .main, in: .common).autoconnect()) { _ in refresh() }
    }

    @ViewBuilder
    private var stagedSection: some View {
        if let plan = displayPlan {
            // Only multi-source clusters carry a grouping decision worth
            // reviewing; single-source units sit behind a collapsible count
            // so they don't crowd the clusters but stay reachable.
            let clusters = plan.groups.filter { $0.members.count > 1 }
            let singles = plan.groups.filter { $0.members.count == 1 }
            let shownClusters = showAllClusters ? clusters : Array(clusters.prefix(ListCap.max))
            LazyVStack(spacing: 1) {
                ForEach(shownClusters) { group in
                    ClusterGroupRow(
                        group: group,
                        model: compilationModel,
                        overrides: $overrides,
                        onCommit: writeOverrides,
                        onRemove: remove,
                        onOpen: openRaw
                    )
                }
            }
            if clusters.count > ListCap.max {
                InlineToggleRow(
                    collapsedLabel: "+ \(clusters.count - ListCap.max) more clusters",
                    isExpanded: showAllClusters
                ) { showAllClusters.toggle() }
            }
            if !singles.isEmpty {
                InlineToggleRow(
                    collapsedLabel: "+ \(singles.count) ungrouped",
                    isExpanded: showSingletons
                ) { showSingletons.toggle() }
                if showSingletons {
                    LazyVStack(spacing: 1) {
                        ForEach(singles) { group in
                            ClusterGroupRow(
                                group: group,
                                model: compilationModel,
                                overrides: $overrides,
                                onCommit: writeOverrides,
                                onRemove: remove,
                                onOpen: openRaw
                            )
                        }
                    }
                }
            }
        } else if store.staged.isEmpty {
            EmptyListMessage(
                text: store.hasState
                    ? "Nothing staged. Files you drop appear here once they're ingested."
                    : nil
            )
        } else {
            PaginatedList(items: store.staged) { source in
                // A too-large source re-defers on every retry--splitting the
                // file is the only fix, so no retry is offered for it.
                let retryable = source.deferReason.map { !$0.hasPrefix("too large") } ?? false
                StagedRow(
                    name: source.displayName,
                    sizeText: source.sizeText,
                    deferReason: source.deferReason,
                    onRetry: retryable ? { retry(source.id) } : nil,
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
            PaginatedList(items: entries) { entry in
                BuildRow(entry: entry, onOpen: { open(entry) })
            }
        }
    }

    private func refresh() {
        let loadedPlan = ClusterPlan.load(config.clusterPlanFile)
        // Reload overrides only when the plan itself changes (a new preview
        // clears them server-side), so in-flight tuning isn't clobbered.
        if loadedPlan?.generatedAt != planStamp {
            planStamp = loadedPlan?.generatedAt ?? ""
            overrides = ClusterOverrides.load(config.clusterOverridesFile)
            showAllClusters = false  // a new grouping starts collapsed
            showSingletons = false
        }
        plan = loadedPlan
        entries = BuildLog.recent(at: config.buildLog)
        compilationModel = ConfigStore.locate(config)
            .map { ConfigStore.load(from: $0).model } ?? "claude-sonnet-4-6"
        loaded = true
    }

    private func previewGrouping() {
        guard let repo = config.repoDir else { return }
        PipelineRunner.runManaged(repoDir: repo, command: "preview-clusters")
    }

    private func writeOverrides() {
        overrides.write(to: config.clusterOverridesFile)
    }

    private func remove(_ rawRel: String) {
        ManifestMutator.removeStagedSource(config: config, rawRel: rawRel)
        refresh()
    }

    /// Requeue a set-aside source. Single-quoted because raw paths carry
    /// spaces and shell metacharacters and the command runs through zsh.
    private func retry(_ rawRel: String) {
        guard let repo = config.repoDir else { return }
        let quoted = "'" + rawRel.replacingOccurrences(of: "'", with: "'\\''") + "'"
        DispatchQueue.global(qos: .userInitiated).async {
            _ = PipelineRunner.runManagedSync(repoDir: repo, command: "recompile \(quoted)")
            DispatchQueue.main.async { refresh() }
        }
    }

    private func openRaw(_ rawRel: String) {
        openInDefaultApp(config.rawRoot.appending(path: rawRel))
    }

    private func open(_ entry: BuildLogEntry) {
        openInDefaultApp(config.wikiRoot.appending(path: entry.relativePath))
    }
}
