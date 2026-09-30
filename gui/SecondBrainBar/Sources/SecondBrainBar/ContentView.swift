import SwiftUI

/// Which pipeline stage the shared scroll area is showing, left to right: the
/// daily pipeline actions (ingest, build) lead, the occasional ChatGPT import
/// follows, and the two wiki-state views (domains, overview) close it out —
/// overview last, as the growth opportunities and health of the compiled wiki.
private enum Tab: String, CaseIterable, Identifiable {
    case ingest = "Ingest"
    case build = "Build"
    case chats = "Chats"
    case domains = "Domains"
    case overview = "Overview"
    var id: String { rawValue }
}

/// Root popover view.
///
/// Layout (top to bottom): the tab bar, the drop zone (Ingest tab only), one
/// scroll area that switches with the selected tab, and a footer with vault
/// stats and actions. The window has a fixed height, so tabs without the drop
/// zone give its space to their list rather than resizing the window. Files
/// can be dropped on any tab; adding them brings the Ingest tab forward.
struct ContentView: View {
    let config: AppConfig
    @State private var tab: Tab = .ingest
    @State private var autoRunner: AutoRunner?
    @State private var showingSettings = false
    @State private var showingNoKeyAlert = false
    @State private var showingBusyAlert = false
    @State private var showingOllamaAlert = false
    @State private var ollamaHealth: PipelineRunner.OllamaHealth?
    @EnvironmentObject private var store: PipelineStore
    @EnvironmentObject private var intake: DropIntake
    @Namespace private var tabNamespace

    var body: some View {
        Group {
            if showingSettings {
                SettingsView(config: config) {
                    withAnimation(.easeInOut(duration: 0.2)) { showingSettings = false }
                }
                .transition(.opacity)
            } else {
                main
            }
        }
        .frame(width: Theme.Metric.popoverWidth, alignment: .topLeading)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(Theme.backgroundGradient)
        .preferredColorScheme(.dark)
        .alert("API key required", isPresented: $showingNoKeyAlert) {
            Button("Open Settings") {
                withAnimation(.easeInOut(duration: 0.2)) { showingSettings = true }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Building the wiki needs an API key for your selected provider. "
                + "Add one in Settings.")
        }
        .alert("A run is already in progress", isPresented: $showingBusyAlert) {
            Button("OK", role: .cancel) {}
        } message: {
            Text("Second Brain is already ingesting or building. Try again when it finishes.")
        }
        .alert("Ollama is required", isPresented: $showingOllamaAlert) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(ollamaHealth?.message
                ?? "Building needs Ollama running with its models downloaded. "
                + "Start Ollama, then try again.")
        }
        .onAppear {
            if autoRunner == nil { autoRunner = AutoRunner(config: config) }
            probeOllama()
        }
        .onChange(of: intake.added) { newValue in
            // A successful drop schedules a debounced (free) ingest run.
            if newValue != nil { autoRunner?.schedule() }
        }
        .onChange(of: intake.submissionCount) { _ in
            withAnimation(.spring(response: 0.28, dampingFraction: 0.82)) { tab = .ingest }
        }
        .onChange(of: showingSettings) { showing in
            // Settings would hide a drop's result, and leaving it to show the
            // result would discard unsaved edits.
            intake.acceptsFiles = !showing
        }
    }

    private var main: some View {
        VStack(alignment: .leading, spacing: 10) {
            tabBar
            if tab == .ingest {
                BinDropZone(bin: .inbox)
                    .transition(.opacity)
            }
            listArea
            footer
        }
        .padding(14)
    }

    /// Custom themed segmented control
    private var tabBar: some View {
        HStack(spacing: 2) {
            ForEach(Tab.allCases) { t in
                tabSegment(t)
            }
        }
        .padding(3)
        .background(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(Theme.Colors.surface)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .strokeBorder(Theme.Colors.stroke, lineWidth: 1)
        )
    }

    private func tabSegment(_ t: Tab) -> some View {
        let selected = tab == t
        return Text(t.rawValue)
            .font(Theme.Font.body(11.5, weight: selected ? .semibold : .regular))
            .foregroundStyle(selected ? Theme.Colors.textPrimary : Theme.Colors.textSecondary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 5)
            .background(
                ZStack {
                    if selected {
                        RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .fill(Theme.Colors.surfaceHover)
                            .matchedGeometryEffect(id: "tabSelection", in: tabNamespace)
                    }
                }
            )
            .contentShape(Rectangle())
            .onTapGesture {
                withAnimation(.spring(response: 0.28, dampingFraction: 0.82)) {
                    tab = t
                }
            }
    }

    @ViewBuilder
    private var listArea: some View {
        ScrollView {
            VStack(spacing: 1) {
                switch tab {
                case .ingest:  IngestTab(config: config)
                case .build:   BuildTab(
                    config: config,
                    onBuild: attemptBuild,
                    canBuild: config.runScriptPath != nil
                )
                case .domains: DomainsTab(config: config)
                case .overview: OverviewTab(config: config)
                case .chats:   ChatsTab(config: config)
                }
            }
            .frame(maxWidth: .infinity, alignment: .topLeading)
            .padding(6)
        }
        .frame(maxHeight: .infinity)
        .background(
            RoundedRectangle(cornerRadius: Theme.Metric.corner, style: .continuous)
                .fill(Theme.Colors.surface.opacity(0.5))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Metric.corner, style: .continuous)
                .strokeBorder(Theme.Colors.stroke, lineWidth: 1)
        )
        .overlay { windowDropTarget }
    }

    /// On tabs without the drop card, a drag over the window needs a visible
    /// target of its own; it looks like the card so the two read as one.
    @ViewBuilder
    private var windowDropTarget: some View {
        if tab != .ingest && intake.drag != .none {
            DropCardFace(
                state: intake.drag == .rejecting ? .rejecting : .accepting,
                idleTitle: "Drop to add to Second Brain",
                hint: Bin.inbox.hint
            )
            .transition(.opacity.animation(.easeOut(duration: 0.12)))
        }
    }

    private var footer: some View {
        HStack(spacing: 9) {
            FooterStatus()
                .layoutPriority(1)
            Spacer(minLength: 6)
            TextAction(title: "Reveal", help: "Show the Second Brain folder in Finder") {
                PipelineRunner.revealInFinder(config.vaultRoot)
            }
            IconAction(systemName: "gearshape", help: "Settings") {
                withAnimation(.easeInOut(duration: 0.2)) { showingSettings = true }
            }
        }
    }

    /// Start a compile, after the busy / API-key / Ollama gates. Passed to the
    /// Build tab, which surfaces it as that tab's primary action.
    private func attemptBuild() {
        if store.isActive {
            showingBusyAlert = true
            return
        }
        let keyName = ConfigStore.locate(config)
            .map { ConfigStore.load(from: $0).llmProvider.envKeyName }
            ?? LLMProvider.anthropic.envKeyName
        guard !EnvStore.readKey(config, keyName: keyName).isEmpty else {
            showingNoKeyAlert = true
            return
        }
        // A detached build discards output, so a missing-Ollama failure would
        // be invisible; gate on the last probe instead.
        if ollamaHealth?.healthy == false {
            showingOllamaAlert = true
            return
        }
        if let url = config.runScriptPath {
            PipelineRunner.runDetached(scriptURL: url, stage: .compile)
        }
    }

    /// Probe Ollama health off the main thread so the build gate and Settings
    /// can reflect whether the local model stack is ready.
    private func probeOllama() {
        guard let repo = config.repoDir else { return }
        DispatchQueue.global(qos: .utility).async {
            let health = PipelineRunner.checkOllama(repoDir: repo)
            DispatchQueue.main.async { ollamaHealth = health }
        }
    }
}

/// Footer left slot: live pipeline status when a run is active, otherwise
/// the vault metrics.
private struct FooterStatus: View {
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        Group {
            if let status = store.status, status.isActive {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small).scaleEffect(0.7)
                        Text(activeDetail(status, now: context.date))
                            .font(Theme.Font.meta(10.5))
                            .foregroundStyle(Theme.Colors.textSecondary)
                            .monospacedDigit()
                    }
                }
            } else {
                Text("\(store.staged.count) staged · \(store.builtCount) built")
                    .font(Theme.Font.meta(10.5))
                    .foregroundStyle(Theme.Colors.textTertiary)
                    .monospacedDigit()
                    .fixedSize()
                    .lineLimit(1)
            }
        }
    }

    // The single live-progress readout: phase + i/n for every stage, with
    // elapsed and cost added for the paid build (the free local stages have
    // no cost to show).
    private func activeDetail(_ status: PipelineStatus, now: Date) -> String {
        let verb: String
        switch status.phase {
        case "compile": verb = "Building"
        case "cluster": verb = "Grouping"
        case "triage":  verb = "Triaging"
        default:        verb = "Ingesting"
        }
        var parts: [String] = [
            status.total > 0
                ? "\(verb) \(min(status.current + 1, status.total))/\(status.total)"
                : verb
        ]
        if status.phase == "compile" {
            parts.append(formatElapsed(max(0, now.timeIntervalSince(status.startedAt))))
            if status.costUSD > 0 { parts.append(String(format: "$%.2f", status.costUSD)) }
        }
        return parts.joined(separator: " · ")
    }
}
