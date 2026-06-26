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

/// A small "?" that reveals an explanation in a popover on click. Used
/// beside specific non-obvious fields
struct HelpButton: View {
    let text: String
    var size: CGFloat = 10
    @State private var show = false
    @State private var hovering = false

    var body: some View {
        Button { show.toggle() } label: {
            Image(systemName: "questionmark.circle")
                .font(.system(size: size))
                .foregroundStyle(hovering ? Theme.Colors.textPrimary : Theme.Colors.textTertiary)
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .popover(isPresented: $show, arrowEdge: .bottom) {
            Text(text)
                .font(Theme.Font.body(12))
                .foregroundStyle(Theme.Colors.textPrimary)
                .frame(width: 260, alignment: .leading)
                .padding(12)
        }
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

/// Standard hoverable row background.
private struct RowBackground: ViewModifier {
    let hovering: Bool
    func body(content: Content) -> some View {
        content
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .background(
                RoundedRectangle(cornerRadius: Theme.Metric.cornerSmall, style: .continuous)
                    .fill(hovering ? Theme.Colors.surfaceHover : Color.clear)
            )
    }
}

/// shows the full text as a tooltip and underlines while the pointer is over the text
private struct OpenableTitle: ViewModifier {
    let full: String
    @State private var hovering = false

    func body(content: Content) -> some View {
        content
            .overlay(alignment: .bottom) {
                if hovering {
                    Rectangle()
                        .frame(height: 1)
                        .foregroundStyle(Theme.Colors.textSecondary)
                }
            }
            .help(full)
            .onHover { hovering = $0 }
    }
}

private extension View {
    func openableTitle(_ full: String) -> some View {
        modifier(OpenableTitle(full: full))
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
                    .buttonStyle(PillButton(tint: Theme.Colors.accentAmber))
            }
            if hovering {
                HoverIcon(systemName: "xmark.circle.fill",
                          help: "Remove (moves the file to Trash)",
                          action: onRemove)
            } else if item.state == .processing {
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
                .buttonStyle(PillButton(tint: Theme.Colors.success))
            Button("Skip", action: onSkip)
                .buttonStyle(PillButton(tint: Theme.Colors.textTertiary))
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

/// Compact capsule button (Keep / Skip / Split …) whose label brightens to
/// white on hover; the capsule fill only deepens on press.
private struct PillButton: ButtonStyle {
    let tint: Color
    func makeBody(configuration: Configuration) -> some View {
        PillBody(configuration: configuration, tint: tint)
    }

    private struct PillBody: View {
        let configuration: ButtonStyleConfiguration
        let tint: Color
        @State private var hovering = false

        // Brighten the fill on hover so the cue works for every tint, including
        // the primary pill whose text is already textPrimary (text-only
        // brightening would be invisible there).
        private var fillOpacity: Double {
            if configuration.isPressed { return 0.34 }
            return hovering ? 0.26 : 0.16
        }

        var body: some View {
            configuration.label
                .font(Theme.Font.meta(10).weight(.medium))
                .lineLimit(1)
                .fixedSize()
                .foregroundStyle(hovering ? Theme.Colors.textPrimary : tint)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(Capsule().fill(tint.opacity(fillOpacity)))
                .onHover { hovering = $0 }
        }
    }
}

/// A borderless icon button whose glyph brightens to white on hover. Row
/// actions use this so hovering a control is clearly highlighted, with no
/// background.
private struct HoverIcon: View {
    let systemName: String
    let help: String
    var size: CGFloat = 11
    var restTint: Color = Theme.Colors.textTertiary
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: size))
                .foregroundStyle(hovering ? Theme.Colors.textPrimary : restTint)
        }
        .buttonStyle(.plain)
        .help(help)
        .onHover { hovering = $0 }
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
    @State private var deferred: [StagedSource] = []
    @State private var plan: ClusterPlan?
    @State private var planStamp = ""
    @State private var overrides = ClusterOverrides.empty
    @State private var showAllClusters = false
    @State private var showSingletons = false
    @State private var entries: [BuildLogEntry] = []
    @State private var status: PipelineStatus?
    @State private var loaded = false
    @State private var compilationModel = "claude-sonnet-4-6"

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

    private var grouping: Bool {
        status?.isActive == true && status?.phase == "cluster"
    }

    var body: some View {
        VStack(spacing: 1) {
            StagedHeader(
                plan: activePlan,
                fallbackCost: VaultData.estimatedBuildCost(staged, model: compilationModel),
                model: compilationModel,
                sourceCount: staged.count,
                status: status,
                grouping: grouping,
                stale: planStale,
                canPreview: config.repoDir != nil
                    && staged.contains { $0.id.hasPrefix("chatgpt/") },
                onPreview: previewGrouping,
                onBuild: onBuild,
                canBuild: canBuild
            )
            if !deferred.isEmpty {
                DeferredNote(count: deferred.count, model: compilationModel)
            }
            stagedSection
            SectionHeader(title: "Recent")
            recentSection
        }
        .onAppear(perform: refresh)
        .onReceive(Timer.publish(every: 1, on: .main, in: .common).autoconnect()) { _ in refresh() }
    }

    @ViewBuilder
    private var stagedSection: some View {
        if staged.isEmpty {
            EmptyListMessage(
                text: loaded
                    ? "Nothing staged. Drop and ingest files, then click Build wiki."
                    : nil
            )
        } else if let plan = activePlan {
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
        deferred = VaultData.deferredSources(config: config)
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
        status = PipelineStatus.read(from: config.statusFile)
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
    let status: PipelineStatus?
    let grouping: Bool
    let stale: Bool
    let canPreview: Bool
    let onPreview: () -> Void
    let onBuild: () -> Void
    let canBuild: Bool

    private var running: Bool {
        grouping || (status?.isActive == true && status?.phase == "compile")
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
            if !running, sourceCount > 0 {
                actions
            }
        }
        .padding(.horizontal, 8).padding(.top, 4).padding(.bottom, 1)
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
                Button((plan == nil && !stale) ? "Group" : "Regroup", action: onPreview)
                    .buttonStyle(PillButton(tint: Theme.Colors.textSecondary))
            }
            if canBuild {
                Button("Build wiki", action: onBuild)
                    .buttonStyle(PillButton(tint: Theme.Colors.textPrimary))
            }
        }
    }
}

/// A line noting sources held out of the build because they exceed the
/// current model's context window. They re-admit automatically on a build with
/// a larger-window model.
private struct DeferredNote: View {
    let count: Int
    let model: String

    var body: some View {
        HStack(spacing: 5) {
            Text("\(count) deferred (too large for \(model))")
                .font(Theme.Font.meta(10))
                .foregroundStyle(Theme.Colors.accentAmber)
            HelpButton(text: "These sources exceed the selected model's context window, so "
                + "they are held out of the build rather than compiled from a lossy summary. "
                + "Switch to a larger-window model and build again to include them.")
            Spacer()
        }
        .padding(.horizontal, 8).padding(.vertical, 1)
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
            Text(cleanName(group.title))
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .help(cleanName(group.title))
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

    // A multi-chat group swaps its cost for a Split/Merge pill on hover. The
    // pill is taller than the text, so both live in a ZStack that always
    // reserves the pill's height, keeping the row from reflowing on hover. A
    // single-source row instead reveals its remove button on hover.
    @ViewBuilder
    private var trailing: some View {
        if isMulti {
            ZStack(alignment: .trailing) {
                cost.opacity(hovering ? 0 : 1)
                Button(isSplit ? "Merge" : "Split") {
                    overrides.toggleSplit(group.id)
                    onCommit()
                }
                .buttonStyle(PillButton(tint: isSplit ? Theme.Colors.success : Theme.Colors.textTertiary))
                .opacity(hovering ? 1 : 0)
                .allowsHitTesting(hovering)
            }
        } else if hovering {
            HoverIcon(systemName: "xmark.circle.fill",
                      help: "Remove from staging (moves the file to Trash)") {
                onRemove(group.members.first?.rel ?? "")
            }
        } else {
            cost
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
                HoverIcon(
                    systemName: "xmark.circle.fill",
                    help: "Remove from staging (moves the file to Trash)",
                    action: onRemove
                )
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
            if hovering {
                HoverIcon(systemName: "xmark.circle.fill",
                          help: "Remove source (moves the raw file to Trash)",
                          action: onRemove)
            } else {
                Text(sizeText)
                    .font(Theme.Font.meta(9.5))
                    .foregroundStyle(Theme.Colors.textTertiary)
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
                .help(domain.name)
            Spacer(minLength: 6)
            menu
                .opacity(hovering && !busy ? 1 : 0)
                .allowsHitTesting(hovering && !busy)
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
