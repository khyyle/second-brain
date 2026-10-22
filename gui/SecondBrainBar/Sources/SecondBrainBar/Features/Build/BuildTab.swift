import SwiftUI

/// Sources staged for compilation, in the order the build takes them — grouped
/// into the build plan when a cluster preview is active — then sources a build
/// set aside, and a log of pages already built.
struct BuildTab: View {
    let config: AppConfig
    let onBuild: () -> Void
    let canBuild: Bool
    @State private var plan: ClusterPlan?
    @State private var planStamp = ""
    @State private var overrides = ClusterOverrides.empty
    @State private var entries: [BuildLogEntry] = []
    @State private var loaded = false
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
                canPreview: config.repoDir != nil
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
        .onReceive(Ticker.everySecond) { _ in refresh() }
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
        let loadedPlan = ClusterPlan.load(config.clusterPlanFile)
        // Reload overrides only when the plan itself changes (a new preview
        // clears them server-side), so in-flight tuning isn't clobbered.
        if loadedPlan?.generatedAt != planStamp {
            planStamp = loadedPlan?.generatedAt ?? ""
            overrides = ClusterOverrides.load(config.clusterOverridesFile)
        }
        plan = loadedPlan
        entries = BuildLog.recent(at: config.buildLog)
        compilationModel = ConfigStore.locate(config)
            .map { ConfigStore.load(from: $0).model } ?? "claude-sonnet-4-6"
        loaded = true
    }

    private func isConversation(_ rel: String) -> Bool {
        ExportProvider.allCases.contains { rel.hasPrefix($0.lane + "/") }
    }

    private func previewGrouping() {
        guard let repo = config.repoDir else { return }
        DispatchQueue.global(qos: .userInitiated).async {
            let result = PipelineRunner.runManagedResult(repoDir: repo, command: "preview-clusters")
            guard !result.succeeded else { return }
            let reason = result.failureReason ?? "The grouping stopped before it finished."
            DispatchQueue.main.async { groupingError = reason }
        }
    }

    private func writeOverrides() {
        overrides.write(to: config.clusterOverridesFile)
    }

    private func remove(_ rawRel: String) {
        withAnimation(.easeOut(duration: 0.3)) { store.removeStagedSource(rawRel) }
    }

    private func skip(_ rawRel: String) {
        ManifestMutator.skipSource(config: config, rawRel: rawRel)
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
