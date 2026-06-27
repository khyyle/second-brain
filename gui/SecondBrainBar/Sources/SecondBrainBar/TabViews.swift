import SwiftUI
import AppKit

/// Global cap on how many rows any list renders. A compact panel can't
/// usefully show a long scroll (Miller's law backs this); the true total is shown
/// separately, and Reveal opens the full set in Finder.
enum ListCap { static let max = 15 }

/// A readable row title: drop the path and extension, and the 8-char
/// disambiguation suffix the conversation parser appends to markdown names.
private func cleanName(_ raw: String) -> String {
    let name = (raw as NSString).lastPathComponent
    let isMarkdown = name.lowercased().hasSuffix(".md")
    var stem = (name as NSString).deletingPathExtension
    if isMarkdown {
        stem = stem.replacingOccurrences(
            of: "-[0-9a-f]{8}$", with: "", options: .regularExpression
        )
    }
    return stem
}

/// Open a file in its default app — markdown opens in Obsidian or an editor,
/// so a row click reads the underlying source or page.
private func openInDefaultApp(_ url: URL) {
    NSWorkspace.shared.open(url)
}

// MARK: - Shared building blocks

/// Small section heading used inside each tab (e.g. "In progress", "Recent").
/// An optional `help` string adds a "?" that reveals an explanation in a
/// popover when clicked.
struct SectionHeader: View {
    let title: String
    var help: String? = nil

    var body: some View {
        HStack(spacing: 5) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.Colors.textTertiary)
            if let help { HelpButton(text: help) }
            Spacer()
        }
        .padding(.horizontal, 8)
        .padding(.top, 4)
        .padding(.bottom, 1)
    }
}

/// A capped list that expands inline in pages — with a collapse — instead of
/// overflowing into Finder. A small overflow fills in with one tap; a large
/// queue pages through, so the popover stays bounded either way.
private struct PaginatedList<Item: Identifiable, RowContent: View>: View {
    let items: [Item]
    var initial: Int = ListCap.max
    var step: Int = 200
    @ViewBuilder let row: (Item) -> RowContent
    @State private var expandedTo = 0  // 0 means the initial window

    private var limit: Int { min(expandedTo == 0 ? initial : expandedTo, items.count) }

    var body: some View {
        LazyVStack(spacing: 1) {
            ForEach(items.prefix(limit)) { row($0) }
        }
        if items.count > limit {
            InlineActionRow(label: "+ \(items.count - limit) more") {
                expandedTo = min(limit + step, items.count)
            }
        }
        if limit > initial {
            InlineActionRow(label: "Show fewer") { expandedTo = 0 }
        }
    }
}

/// A left-aligned inline expand/collapse toggle, e.g. "+ 12 more clusters"
/// or "+ 224 ungrouped", collapsing back to "Show fewer". Used for both the
/// extra clusters and the single-source units, which are computed rather than
/// files, so they expand inline rather than opening Finder.
private struct InlineToggleRow: View {
    let collapsedLabel: String
    let isExpanded: Bool
    let onToggle: () -> Void

    var body: some View {
        InlineActionRow(label: isExpanded ? "Show fewer" : collapsedLabel, action: onToggle)
    }
}

/// A left-aligned, low-emphasis action row: meta text that brightens on hover
private struct InlineActionRow: View {
    let label: String
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Text(label)
                .font(Theme.Font.meta(10))
                .foregroundStyle(hovering ? Theme.Colors.textSecondary : Theme.Colors.textTertiary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}

/// Shared empty / loading state for a section.
struct EmptyListMessage: View {
    let text: String?
    var body: some View {
        Group {
            if let text {
                Text(text)
                    .font(Theme.Font.body(11.5))
                    .foregroundStyle(Theme.Colors.textSecondary)
            } else {
                ProgressView().controlSize(.small)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
    }
}

private func relativeTime(_ date: Date) -> String {
    let interval = Date().timeIntervalSince(date)
    if interval < 60      { return "now" }
    if interval < 3600    { return "\(Int(interval / 60))m" }
    if interval < 86_400  { return "\(Int(interval / 3600))h" }
    if interval < 604_800 { return "\(Int(interval / 86_400))d" }
    return "\(Int(interval / 604_800))w"
}

// MARK: - Ingest tab

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
                    help: "These couldn't be parsed. Retry re-runs the local "
                        + "parser; the X removes the file."
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
        .onReceive(Timer.publish(every: 1, on: .main, in: .common).autoconnect()) { _ in refresh() }
    }

    private func refresh() {
        queue = VaultData.queue(config: config)
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

private struct QueueRow: View {
    let item: QueueItem
    let onOpen: () -> Void
    let onRemove: () -> Void
    var onRetry: (() -> Void)? = nil
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 9) {
            Group {
                if item.state == .processing {
                    ProgressView().controlSize(.small).scaleEffect(0.6)
                } else {
                    Image(systemName: item.state == .failed ? "exclamationmark.triangle" : "clock")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(item.state == .failed ? Theme.Colors.danger : Theme.Colors.textTertiary)
                }
            }
            .frame(width: 12)
            Text(cleanName(item.displayName))
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(item.displayName))
            Spacer(minLength: 6)
            if item.state == .failed, let onRetry {
                Button("Retry", action: onRetry)
                    .buttonStyle(PillButton(.warning))
                    .disabled(store.locked)
            }
            TrailingReserve(hovering: hovering) {
                if item.state == .processing {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        Text(formatElapsed(max(0, context.date.timeIntervalSince(item.since))))
                            .font(Theme.Font.meta(10))
                            .foregroundStyle(Theme.Colors.textTertiary)
                            .monospacedDigit()
                    }
                } else if item.state == .waiting {
                    Text(item.state.label)
                        .font(Theme.Font.meta(10))
                        .foregroundStyle(Theme.Colors.textTertiary)
                        .monospacedDigit()
                }
            } hover: {
                HoverIcon(systemName: "xmark.circle.fill",
                          help: "Remove (moves the file to Trash)",
                          action: onRemove)
                    .disabled(store.locked)
            }
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
    }
}

// MARK: - Chats tab

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
                help: "Triage filters bulk ChatGPT conversation imports into "
                    + "worthwhile / review / skip using a local model, so only "
                    + "substantive chats become wiki pages. Items it's unsure "
                    + "about land here for your call."
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

private struct ReviewRow: View {
    let row: TriageRow
    let onOpen: () -> Void
    let onKeep: () -> Void
    let onSkip: () -> Void
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: "questionmark.circle")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Theme.Colors.accentAmber)
                .frame(width: 12)
            Text(cleanName(row.displayName))
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(row.displayName))
            Spacer(minLength: 6)
            Button("Keep", action: onKeep)
                .buttonStyle(PillButton(.confirm))
                .disabled(store.locked)
            Button("Skip", action: onSkip)
                .buttonStyle(PillButton(.quiet))
                .disabled(store.locked)
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
    }
}

private struct TriageDecidedRow: View {
    let row: TriageRow
    let onOpen: () -> Void
    let onSkip: () -> Void
    let onUnskip: () -> Void
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 7) {
            Text(cleanName(row.displayName))
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(row.displayName))
            Spacer(minLength: 6)
            // The action fades in beside the badge (kept always present so the
            // status never appears to flip and the row doesn't reflow).
            hoverAction
                .opacity(hovering ? 1 : 0)
                .allowsHitTesting(hovering)
                .disabled(store.locked)
            badge
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
        .animation(.easeInOut(duration: 0.12), value: hovering)
    }

    @ViewBuilder
    private var hoverAction: some View {
        switch row.decision {
        case .worthwhile:
            HoverIcon(systemName: "minus.circle", help: "Skip — set aside",
                      size: 12, action: onSkip)
        case .skip:
            HoverIcon(systemName: "plus.circle", help: "Keep — restore to the build",
                      size: 12, restTint: Theme.Colors.success, action: onUnskip)
        case .review:
            EmptyView()
        }
    }

    private var badge: some View {
        Text(row.decision.label)
            .font(Theme.Font.meta(9.5).weight(.medium))
            .foregroundStyle(badgeColor)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(Capsule().fill(badgeColor.opacity(0.14)))
    }

    private var badgeColor: Color {
        switch row.decision {
        case .worthwhile: return Theme.Colors.success
        case .review:     return Theme.Colors.accentAmber
        case .skip:       return Theme.Colors.textTertiary
        }
    }
}

// MARK: - Build tab

/// Sources staged for compilation — grouped into the build plan when a
/// cluster preview is active — plus a log of pages already built.
struct BuildTab: View {
    let config: AppConfig
    let onBuild: () -> Void
    let canBuild: Bool
    @State private var staged: [StagedSource] = []
    @State private var plan: ClusterPlan?
    @State private var planStamp = ""
    @State private var overrides = ClusterOverrides.empty
    @State private var showAllClusters = false
    @State private var showSingletons = false
    @State private var entries: [BuildLogEntry] = []
    @State private var loaded = false
    @State private var compilationModel = "claude-sonnet-4-6"
    @EnvironmentObject private var store: PipelineStore

    // A fresh plan drives the view (and the build honors it) as long as it
    // still covers exactly the staged set. Producing a plan is itself the
    // intent to cluster, so it isn't gated on the config flag.
    private var activePlan: ClusterPlan? {
        guard let plan, plan.memberPaths == Set(staged.map(\.id)) else { return nil }
        return plan
    }

    // A plan exists but no longer matches the staged set, so its grouping
    // and cost are out of date until regrouped.
    private var planStale: Bool {
        plan != nil && activePlan == nil
    }

    // The grouped view stays visible (read-only) while a compile runs, when the
    // staged set is shrinking and would otherwise drop the matched plan.
    private var displayPlan: ClusterPlan? {
        store.isCompiling ? plan : activePlan
    }

    var body: some View {
        VStack(spacing: 1) {
            StagedHeader(
                plan: activePlan,
                fallbackCost: VaultData.estimatedBuildCost(staged, model: compilationModel),
                model: compilationModel,
                sourceCount: staged.count,
                stale: planStale,
                canPreview: config.repoDir != nil
                    && staged.contains { $0.id.hasPrefix("chatgpt/") },
                onPreview: previewGrouping,
                onBuild: onBuild,
                canBuild: canBuild
            )
            stagedSection
            SectionHeader(title: "Recent")
            recentSection
        }
        .onAppear(perform: refresh)
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
        } else if staged.isEmpty {
            EmptyListMessage(
                text: loaded
                    ? "Nothing staged. Drop and ingest files, then click Build wiki."
                    : nil
            )
        } else {
            PaginatedList(items: staged) { source in
                StagedRow(
                    name: source.displayName,
                    sizeText: source.sizeText,
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
        staged = VaultData.stagedSources(config: config)
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

    private func openRaw(_ rawRel: String) {
        openInDefaultApp(config.rawRoot.appending(path: rawRel))
    }

    private func open(_ entry: BuildLogEntry) {
        openInDefaultApp(config.wikiRoot.appending(path: entry.relativePath))
    }
}

/// "Staged for build" heading. Shows staged count + cost (the plan's when a
/// cluster preview is active, else a per-source estimate), a Preview/Refresh
/// action, and live progress while grouping or compiling.
private struct StagedHeader: View {
    let plan: ClusterPlan?
    let fallbackCost: Double
    let model: String
    let sourceCount: Int
    let stale: Bool
    let canPreview: Bool
    let onPreview: () -> Void
    let onBuild: () -> Void
    let canBuild: Bool
    @EnvironmentObject private var store: PipelineStore

    private var running: Bool {
        store.isGrouping || store.isCompiling
    }

    // A stale plan's cost is for the old staged set, so fall back to the
    // per-source estimate, which always reflects what is currently staged.
    private var estimatedCost: Double {
        stale ? fallbackCost : (plan?.cost(for: model) ?? fallbackCost)
    }

    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 5) {
                    Text("Staged for build")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.Colors.textTertiary)
                    HelpButton(text: "Ingested files that will be compiled when build is selected. 'Group' (chats only) "
                        + "bundles related conversation into one compilation to avoid duplicate calls/pages. "
                        + "Cost is a rough upper bound.")
                }
                if !running, sourceCount > 0 {
                    costLine
                }
            }
            Spacer(minLength: 8)
            if store.isCompiling || store.stopping {
                stopButton
            } else if !running, sourceCount > 0 {
                actions
            }
        }
        .padding(.horizontal, 8).padding(.top, 4).padding(.bottom, 1)
    }

    /// While a compile runs, the primary action becomes Stop in the same slot
    /// the Build button occupied — then a disabled Stopping until it winds down.
    private var stopButton: some View {
        Button(store.stopping ? "Stopping" : "Stop", action: store.requestStop)
            .buttonStyle(PillButton(.destructive))
            .disabled(store.stopping)
    }

    private var costLine: some View {
        Text("~$\(String(format: "%.2f", estimatedCost))")
            .font(Theme.Font.meta(10))
            .foregroundStyle(Theme.Colors.textTertiary)
            .monospacedDigit()
    }

    private var actions: some View {
        HStack(spacing: 8) {
            if canPreview {
                regroupButton
            }
            if canBuild {
                Button("Build wiki", action: onBuild)
                    .buttonStyle(PillButton(.primary))
            }
        }
    }

    // A stale plan no longer matches the staged set, so its grouping — and the
    // cost saving that comes with it — is dropped on the next build unless it is
    // recomputed first. Mark that case amber with a refresh glyph; an absent or
    // current plan stays neutral.
    @ViewBuilder
    private var regroupButton: some View {
        if stale {
            Button(action: onPreview) {
                HStack(spacing: 3) {
                    Image(systemName: "arrow.triangle.2.circlepath")
                    Text("Regroup")
                }
            }
            .buttonStyle(PillButton(tint: Theme.Colors.accentAmber))
        } else {
            Button(plan == nil ? "Group" : "Regroup", action: onPreview)
                .buttonStyle(PillButton(.neutral))
        }
    }
}

/// A build plan unit: one expandable group of related chats (with split and
/// per-source pop-out tuning), or a plain row for a single-source unit.
private struct ClusterGroupRow: View {
    let group: ClusterGroup
    let model: String
    @Binding var overrides: ClusterOverrides
    let onCommit: () -> Void
    let onRemove: (String) -> Void
    let onOpen: (String) -> Void
    @State private var expanded = false
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    private var isMulti: Bool { group.members.count > 1 }
    private var isSplit: Bool { overrides.isSplit(group.id) }

    var body: some View {
        VStack(spacing: 1) {
            header
            if expanded {
                ForEach(group.members, id: \.rel) { member in
                    ClusterMemberRow(
                        member: member,
                        excluded: overrides.isExcluded(member.rel),
                        onToggleExclude: {
                            overrides.toggleExcluded(member.rel)
                            onCommit()
                        },
                        onRemove: { onRemove(member.rel) },
                        onOpen: { onOpen(member.rel) }
                    )
                }
            }
        }
    }

    private var header: some View {
        HStack(spacing: 9) {
            Image(systemName: isMulti ? (expanded ? "chevron.down" : "chevron.right") : "circle.dashed")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Theme.Colors.textSecondary)
                .frame(width: 12)
            title
            if isMulti {
                Text("\(group.members.count) chats" + (isSplit ? " · split" : ""))
                    .font(Theme.Font.meta(9.5))
                    .foregroundStyle(isSplit ? Theme.Colors.accentAmber : Theme.Colors.textTertiary)
            }
            Spacer(minLength: 6)
            trailing
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture {
            if isMulti { expanded.toggle() } else { onOpen(group.members.first?.rel ?? "") }
        }
        .onHover { hovering = $0 }
    }

    // A single-source unit opens its file on click, so its title carries the
    // openable underline affordance; a multi-chat group's title toggles the
    // group open instead, so it only gets a tooltip.
    @ViewBuilder
    private var title: some View {
        let name = cleanName(group.title)
        let base = Text(name)
            .font(Theme.Font.body(11.5))
            .foregroundStyle(Theme.Colors.textPrimary)
            .lineLimit(1)
            .truncationMode(.middle)
        if isMulti {
            base.help(name)
        } else {
            base.openableTitle(name)
        }
    }

    // A multi-chat group swaps its cost for a Split/Merge pill on hover. The
    // pill is taller than the text, so both live in a ZStack that always
    // reserves the pill's height, keeping the row from reflowing on hover. A
    // single-source row instead reveals its remove button on hover.
    @ViewBuilder
    private var trailing: some View {
        if isMulti {
            TrailingReserve(hovering: hovering) {
                cost
            } hover: {
                Button(isSplit ? "Merge" : "Split") {
                    overrides.toggleSplit(group.id)
                    onCommit()
                }
                .buttonStyle(PillButton(isSplit ? .confirm : .quiet))
                .disabled(store.locked)
            }
        } else {
            TrailingReserve(hovering: hovering) {
                cost
            } hover: {
                HoverIcon(systemName: "xmark.circle.fill",
                          help: "Remove from staging (moves the file to Trash)") {
                    onRemove(group.members.first?.rel ?? "")
                }
                .disabled(store.locked)
            }
        }
    }

    private var cost: some View {
        Text(String(format: "~$%.2f", group.cost(for: model)))
            .font(Theme.Font.meta(9.5))
            .foregroundStyle(Theme.Colors.textTertiary)
            .monospacedDigit()
    }
}

/// One chat inside an expanded group. Hovering reveals two actions: pop it
/// out so it compiles as its own page, or remove it from staging. An amber
/// "own page" tag marks a chat already popped out.
private struct ClusterMemberRow: View {
    let member: ClusterMember
    let excluded: Bool
    let onToggleExclude: () -> Void
    let onRemove: () -> Void
    let onOpen: () -> Void
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 9) {
            Spacer().frame(width: 16)
            Text(cleanName(member.rel))
                .font(Theme.Font.body(11))
                .foregroundStyle(excluded ? Theme.Colors.textTertiary : Theme.Colors.textSecondary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(member.rel))
            Spacer(minLength: 6)
            if hovering {
                HoverIcon(
                    systemName: excluded ? "arrow.uturn.left.circle" : "arrow.up.right.circle",
                    help: excluded ? "Put back in this group"
                                   : "Compile on its own page instead of in this group",
                    action: onToggleExclude
                )
                .disabled(store.locked)
                HoverIcon(
                    systemName: "xmark.circle.fill",
                    help: "Remove from staging (moves the file to Trash)",
                    action: onRemove
                )
                .disabled(store.locked)
            } else if excluded {
                Text("own page")
                    .font(Theme.Font.meta(9))
                    .foregroundStyle(Theme.Colors.accentAmber)
            }
        }
        .padding(.horizontal, 8).padding(.vertical, 3)
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
    }
}

/// One source waiting to be compiled; hover to un-ingest it.
private struct StagedRow: View {
    let name: String
    let sizeText: String
    let onOpen: () -> Void
    let onRemove: () -> Void
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: "circle.dashed")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Theme.Colors.accentAmber)
                .frame(width: 12)
            Text(cleanName(name))
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(name))
            Spacer(minLength: 6)
            TrailingReserve(hovering: hovering) {
                Text(sizeText)
                    .font(Theme.Font.meta(9.5))
                    .foregroundStyle(Theme.Colors.textTertiary)
            } hover: {
                HoverIcon(systemName: "xmark.circle.fill",
                          help: "Remove source (moves the raw file to Trash)",
                          action: onRemove)
                    .disabled(store.locked)
            }
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
    }
}

private struct BuildRow: View {
    let entry: BuildLogEntry
    let onOpen: () -> Void
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: entry.action == .created ? "plus.circle" : "pencil")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(entry.action == .created ? Theme.Colors.success : Theme.Colors.textSecondary)
                .frame(width: 12)
            Text(verb)
                .font(Theme.Font.meta(10))
                .foregroundStyle(Theme.Colors.textTertiary)
            Text(cleanName(entry.pageName))
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(entry.pageName))
            Spacer(minLength: 6)
            Text(relativeTime(entry.at))
                .font(Theme.Font.meta(10.5))
                .foregroundStyle(Theme.Colors.textTertiary)
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
    }

    private var verb: String { entry.action == .created ? "created" : "updated" }
}

// MARK: - Domains tab

/// The wiki's domain vocabulary, with rename / merge / delete. Domains are
/// frontmatter metadata the compilation agent grows over time; this is where
/// the user curates that vocabulary without editing the wiki by hand.
struct DomainsTab: View {
    let config: AppConfig
    @State private var domains: [DomainInfo] = []
    @State private var loaded = false
    @State private var unavailable = false
    @State private var busy = false
    @State private var renaming: DomainInfo?
    @State private var renameText = ""
    @State private var deleting: DomainInfo?

    var body: some View {
        VStack(spacing: 1) {
            header
            content
        }
        .onAppear(perform: refresh)
        .alert("Rename domain", isPresented: renamePresented) {
            TextField("New name", text: $renameText)
            Button("Rename", action: performRename)
            Button("Cancel", role: .cancel) { renaming = nil }
        } message: {
            Text(renaming.map { "Rename '\($0.name)' across \($0.pageCount) page(s)." } ?? "")
        }
        .alert("Delete domain", isPresented: deletePresented) {
            Button("Delete", role: .destructive, action: performDelete)
            Button("Cancel", role: .cancel) { deleting = nil }
        } message: {
            Text(
                deleting.map {
                    "Remove '\($0.name)' from \($0.pageCount) page(s)? The pages stay, "
                        + "only this domain is removed from their frontmatter."
                } ?? ""
            )
        }
    }

    private var header: some View {
        HStack(spacing: 5) {
            Text("Domains")
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.Colors.textTertiary)
            HelpButton(
                text: "Domains are broad subject areas tagged in each page's frontmatter, "
                    + "grown by the compilation agent as it builds. Edits rewrite every affected page"
            )
            Spacer()
            if busy { ProgressView().controlSize(.small).scaleEffect(0.7) }
        }
        .padding(.horizontal, 8).padding(.top, 4).padding(.bottom, 1)
    }

    @ViewBuilder
    private var content: some View {
        if !loaded {
            EmptyListMessage(text: nil)
        } else if unavailable {
            EmptyListMessage(text: "Domains need the installed pipeline. Reinstall to manage them.")
        } else if domains.isEmpty {
            EmptyListMessage(text: "No domains yet. They appear once you build the wiki.")
        } else {
            ForEach(domains) { domain in
                DomainRow(
                    domain: domain,
                    others: domains.map(\.name).filter { $0 != domain.name },
                    busy: busy,
                    onOpen: { open(domain) },
                    onRename: { startRename(domain) },
                    onMerge: { merge(domain, into: $0) },
                    onDelete: { deleting = domain }
                )
            }
        }
    }

    private var renamePresented: Binding<Bool> {
        Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })
    }

    private var deletePresented: Binding<Bool> {
        Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })
    }

    private func startRename(_ domain: DomainInfo) {
        renameText = domain.name
        renaming = domain
    }

    private func performRename() {
        guard let target = renaming else { return }
        let newName = renameText.trimmingCharacters(in: .whitespacesAndNewlines)
        renaming = nil
        guard !newName.isEmpty, newName != target.name else { return }
        mutate { DomainMutator.rename(config: config, old: target.name, new: newName, completion: $0) }
    }

    private func performDelete() {
        guard let target = deleting else { return }
        deleting = nil
        mutate { DomainMutator.delete(config: config, name: target.name, completion: $0) }
    }

    private func merge(_ domain: DomainInfo, into dest: String) {
        mutate {
            DomainMutator.merge(config: config, sources: [domain.name], dest: dest, completion: $0)
        }
    }

    /// Run a mutation with the busy spinner up, then refresh the list.
    private func mutate(_ action: (@escaping (Bool) -> Void) -> Void) {
        busy = true
        action { _ in
            busy = false
            refresh()
        }
    }

    private func open(_ domain: DomainInfo) {
        openInDefaultApp(config.wikiRoot.appending(path: "_views/domains/\(domain.name).md"))
    }

    private func refresh() {
        guard config.repoDir != nil else {
            unavailable = true
            loaded = true
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let result = DomainData.load(config: config)
            DispatchQueue.main.async {
                if let result {
                    domains = result
                    unavailable = false
                } else {
                    unavailable = true
                }
                loaded = true
            }
        }
    }
}

/// One domain row: its name, page count, and a hover menu for rename / merge /
/// delete. Tapping the row opens its generated domain view.
private struct DomainRow: View {
    let domain: DomainInfo
    let others: [String]
    let busy: Bool
    let onOpen: () -> Void
    let onRename: () -> Void
    let onMerge: (String) -> Void
    let onDelete: () -> Void
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: "tag")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Theme.Colors.textTertiary)
                .frame(width: 12)
            Text(domain.name)
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(domain.name)
            Spacer(minLength: 6)
            menu
                .opacity(hovering && !busy ? 1 : 0)
                .allowsHitTesting(hovering && !busy)
                .disabled(store.locked)
            count
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
        .animation(.easeInOut(duration: 0.12), value: hovering)
    }

    private var count: some View {
        Text("\(domain.pageCount)")
            .font(Theme.Font.meta(9.5))
            .foregroundStyle(Theme.Colors.textTertiary)
            .help(domain.pageCount == 1 ? "1 page" : "\(domain.pageCount) pages")
    }

    private var menu: some View {
        Menu {
            Button("Rename…", action: onRename)
            if !others.isEmpty {
                Menu("Merge into") {
                    ForEach(others, id: \.self) { other in
                        Button(other) { onMerge(other) }
                    }
                }
            }
            Divider()
            Button("Delete", role: .destructive, action: onDelete)
        } label: {
            Image(systemName: "ellipsis.circle")
                .font(.system(size: 11))
                .foregroundStyle(Theme.Colors.textSecondary)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .frame(width: 16)
    }
}

/// Read-only structural health of the compiled wiki: the checks from
/// `second-brain health`, shown as a compact table. A check with issues
/// expands inline to the pages it flagged, which open on click. There are no
/// action buttons — every fix lands on the next build, which is the Build tab.
struct HealthTab: View {
    let config: AppConfig
    @State private var health: WikiHealth?
    @State private var loaded = false
    @State private var unavailable = false

    var body: some View {
        VStack(spacing: 1) {
            SectionHeader(
                title: "Health",
                help: """
                Orphan pages — nothing links to them.
                Gaps — links to pages not written yet.
                Oversized pages — over 4000 words; split candidates.
                Stub pages — under 150 words.
                Missing frontmatter — no title, type, or domains.
                Stale pages — source changed since the last build.
                """
            )
            content
        }
        .onAppear(perform: refresh)
    }

    @ViewBuilder
    private var content: some View {
        if !loaded {
            EmptyListMessage(text: nil)
        } else if unavailable {
            EmptyListMessage(text: "Health needs the installed pipeline. Reinstall to view it.")
        } else if let health {
            ForEach(health.categories) { category in
                HealthCategoryRow(category: category, onOpen: open)
            }
        }
    }

    /// Open a flagged page by stem; the flat wiki keeps stems unique, so the
    /// first content folder that has it wins.
    private func open(_ stem: String) {
        for dir in ["concepts", "problems", "projects", "insights", "syntheses"] {
            let url = config.wikiRoot.appending(path: "\(dir)/\(stem).md")
            if FileManager.default.fileExists(atPath: url.path) {
                openInDefaultApp(url)
                return
            }
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

/// One health check as a table row: its label and flagged count. A passing
/// check reads as a quiet check mark; a check with issues expands inline.
private struct HealthCategoryRow: View {
    let category: HealthCategory
    let onOpen: (String) -> Void
    @State private var expanded = false
    @State private var hovering = false

    private var hasIssues: Bool { category.count > 0 }

    /// What this check means, surfaced as a hover tooltip in place of a header
    /// help button — the labels carry the rest.
    private var explanation: String {
        switch category.key {
        case "orphan_pages": return "Pages nothing links to."
        case "gap_links": return "Links to pages not written yet."
        case "oversized_pages": return "Pages over 4000 words — candidates to split."
        case "undersized_pages": return "Pages under 150 words."
        case "missing_frontmatter": return "Pages missing a title, type, or domains."
        case "stale_pages": return "Pages whose source changed since the last build."
        default: return category.label
        }
    }

    var body: some View {
        VStack(spacing: 1) {
            row
            if expanded {
                PaginatedList(items: category.items) { item in
                    HealthItemRow(item: item, onOpen: onOpen)
                }
            }
        }
    }

    private var row: some View {
        HStack(spacing: 9) {
            Image(systemName: hasIssues ? "chevron.right" : "checkmark")
                .font(.system(size: 9, weight: .semibold))
                .foregroundStyle(hasIssues ? Theme.Colors.textTertiary : Theme.Colors.success)
                .rotationEffect(.degrees(expanded ? 90 : 0))
                .frame(width: 12)
            Text(category.label)
                .font(Theme.Font.body(11.5))
                .foregroundStyle(hasIssues ? Theme.Colors.textPrimary : Theme.Colors.textSecondary)
            Spacer(minLength: 6)
            Text("\(category.count)")
                .font(Theme.Font.meta(9.5))
                .foregroundStyle(hasIssues ? Theme.Colors.textSecondary : Theme.Colors.textTertiary)
        }
        .modifier(RowBackground(hovering: hovering && hasIssues))
        .contentShape(Rectangle())
        .onTapGesture {
            guard hasIssues else { return }
            withAnimation(.easeInOut(duration: 0.15)) { expanded.toggle() }
        }
        .onHover { hovering = $0 }
        .help(explanation)
        .animation(.easeInOut(duration: 0.12), value: hovering)
    }
}

/// One flagged item under an expanded check. Page-backed items open on click;
/// a broken link points at a missing page, so it stays inert text.
private struct HealthItemRow: View {
    let item: HealthItem
    let onOpen: (String) -> Void
    @State private var hovering = false

    private var openable: Bool { item.page != nil }

    var body: some View {
        HStack(spacing: 9) {
            Spacer().frame(width: 12)
            title
            Spacer(minLength: 6)
        }
        .modifier(RowBackground(hovering: hovering && openable))
        .contentShape(Rectangle())
        .onTapGesture { if let page = item.page { onOpen(page) } }
        .onHover { hovering = $0 }
        .animation(.easeInOut(duration: 0.12), value: hovering)
    }

    // Page-backed items open on click, so they carry the openable underline;
    // an item with no page (a broken link's missing target) stays inert text.
    @ViewBuilder
    private var title: some View {
        let base = Text(item.text)
            .font(Theme.Font.body(11))
            .foregroundStyle(openable ? Theme.Colors.textSecondary : Theme.Colors.textTertiary)
            .lineLimit(1)
            .truncationMode(.middle)
        if openable {
            base.openableTitle(item.text)
        } else {
            base.help(item.text)
        }
    }
}
