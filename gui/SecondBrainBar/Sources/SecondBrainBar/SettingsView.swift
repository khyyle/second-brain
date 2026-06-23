import SwiftUI
import AppKit

/// In-popover settings panel. Scalar knobs are written straight into
/// `config.yaml`; watched folders go to the GUI-owned `sources.json`;
/// scheduling and MCP wiring shell out to the CLI so the choices take
/// effect. Help "?" buttons sit beside individual non-obvious fields,
/// never as a section summary.
struct SettingsView: View {
    let config: AppConfig
    let onClose: () -> Void

    @State private var settings = PipelineSettings.fallback
    @State private var scheduleHours: [Int] = []
    @State private var scheduleEnabled = false
    @State private var watched: [WatchedFolder] = []
    @State private var connectStatus: [String: ConnectStatus] = [:]
    @State private var apiKey = ""
    @State private var loadedKey = ""
    @State private var costCap = ""
    @State private var configURL: URL?

    private let profileLabels: [String: String] = [
        "balanced": "Balanced",
        "technical": "Technical",
        "skip_heavy": "Skip-heavy",
        "project_heavy": "Project-heavy",
        "lenient": "Lenient",
    ]

    /// Switching provider resets the model to that provider's default and
    /// reloads the key field to show the newly-selected provider's key.
    private var providerBinding: Binding<LLMProvider> {
        Binding(
            get: { settings.llmProvider },
            set: { newProvider in
                settings.provider = newProvider.rawValue
                settings.model = newProvider.defaultModel
                loadedKey = EnvStore.readKey(config, keyName: newProvider.envKeyName)
                apiKey = loadedKey
            }
        )
    }

    private var modelBinding: Binding<String> {
        Binding(get: { settings.model }, set: { settings.model = $0 })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if configURL == nil {
                unavailable
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) { groups }
                        .padding(.bottom, 2)
                }
                .frame(maxHeight: 384)
            }
            footer
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .onAppear(perform: load)
    }

    private var header: some View {
        HStack(spacing: 7) {
            Image(systemName: "gearshape.fill")
                .font(.system(size: 11))
                .foregroundStyle(Theme.Colors.textSecondary)
            Text("Settings")
                .font(Theme.Font.body(13, weight: .semibold))
                .foregroundStyle(Theme.Colors.textPrimary)
        }
    }

    private var unavailable: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("Config not found")
                .font(Theme.Font.body(12, weight: .semibold))
                .foregroundStyle(Theme.Colors.textPrimary)
            Text("Run the installer so the app can locate config.yaml.")
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, 8)
    }

    @ViewBuilder
    private var groups: some View {
        SettingsGroup(
            title: "Compilation provider",
            help: "Which cloud model builds the wiki. Anthropic runs Claude; "
                + "DeepSeek is cheaper and uses its Anthropic-compatible API. "
                + "Each provider has its own API key below."
        ) {
            SegControl(
                options: LLMProvider.allCases.map { ($0.displayName, $0) },
                selection: providerBinding
            )
            if settings.llmProvider.models.count > 1 {
                Text("Model")
                    .font(Theme.Font.meta(10))
                    .foregroundStyle(Theme.Colors.textSecondary)
                SegControl(
                    options: settings.llmProvider.models.map { ($0.label, $0.id) },
                    selection: modelBinding
                )
            }
        }

        SettingsGroup(
            title: "\(settings.llmProvider.displayName) API key",
            help: "Used to compile the wiki. Stored locally in a .env--"
        ) {
            SecureField(settings.llmProvider.keyPlaceholder, text: $apiKey)
                .textFieldStyle(.plain)
                .font(Theme.Font.meta(11))
                .foregroundStyle(Theme.Colors.textPrimary)
                .padding(.horizontal, 8).padding(.vertical, 5)
                .background(
                    RoundedRectangle(cornerRadius: 6, style: .continuous)
                        .fill(Theme.Colors.background)
                )
            if apiKey == loadedKey && !loadedKey.isEmpty {
                Row("Status") {
                    Text("Key set")
                        .font(Theme.Font.meta(10))
                        .foregroundStyle(Theme.Colors.success)
                }
            }
        }

        SettingsGroup(
            title: "Build",
            help: "Stops the build before the next document once a run's estimated cost "
                + "crosses it. Finished pages will be kept and the rest staged. "
                + "Leave blank for no limit."
        ) {
            Row("Spend cap per build") {
                HStack(spacing: 3) {
                    Text("$")
                        .font(Theme.Font.meta(11))
                        .foregroundStyle(Theme.Colors.textSecondary)
                    TextField("none", text: $costCap)
                        .textFieldStyle(.plain)
                        .font(Theme.Font.meta(11))
                        .foregroundStyle(Theme.Colors.textPrimary)
                        .multilineTextAlignment(.trailing)
                        .frame(width: 52)
                        .padding(.horizontal, 6).padding(.vertical, 4)
                        .background(
                            RoundedRectangle(cornerRadius: 6, style: .continuous)
                                .fill(Theme.Colors.background)
                        )
                }
            }
        }

        SettingsGroup(title: "Triage") {
            Row("Filter ChatGPT imports") {
                Toggle("", isOn: $settings.triageEnabled)
                    .labelsHidden().toggleStyle(.switch).tint(Theme.Colors.accent)
            }
            Row("Style",
                help: "How readily triage keeps versus skips a chat. "
                    + "Technical favors STEM and code, Project-heavy "
                    + "favors project notes, Skip-heavy and Lenient "
                    + "shift the bar down and up.") {
                Picker("", selection: $settings.triageProfile) {
                    ForEach(PipelineSettings.profiles, id: \.self) { p in
                        Text(profileLabels[p] ?? p).tag(p)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .font(Theme.Font.body(11.5))
                .tint(Theme.Colors.textSecondary)
                .disabled(!settings.triageEnabled)
                .opacity(settings.triageEnabled ? 1 : 0.4)
            }
        }

        SettingsGroup(title: "MCP") {
            Row("Semantic search",
                help: "Adds embedding-based search (via Ollama) on top of keyword "
                    + "search for MCP clients. Falls back to keyword if Ollama is "
                    + "unavailable.") {
                Toggle("", isOn: $settings.semanticEnabled)
                    .labelsHidden().toggleStyle(.switch).tint(Theme.Colors.accent)
            }
            HStack(spacing: 8) {
                ConnectButton(title: "Claude Desktop",
                              status: connectStatus["claude-desktop"] ?? .idle) {
                    connect("claude-desktop")
                }
                ConnectButton(title: "Cursor",
                              status: connectStatus["cursor"] ?? .idle) {
                    connect("cursor")
                }
                Spacer(minLength: 0)
            }
        }

        SettingsGroup(title: "Automation") {
            Row("Run automatically",
                help: "Runs ingest and build in the background at the times below, "
                    + "via macOS launchd. If your computer is asleep at a scheduled "
                    + "time the run happens on next wake.") {
                Toggle("", isOn: $scheduleEnabled)
                    .labelsHidden().toggleStyle(.switch).tint(Theme.Colors.accent)
            }
            if scheduleEnabled {
                watchedFolders
                subhead("Schedule")
                ScheduleEditor(hours: $scheduleHours)
            }
        }
    }

    @ViewBuilder
    private var watchedFolders: some View {
        subhead("Watched folders",
                help: "Extra folders swept on each scheduled run, on top of the "
                    + "files you drop into the app. Unlike a drop they aren't "
                    + "ingested the moment a file lands; the next scheduled run "
                    + "picks them up. Large handwritten folders can be slow "
                    + "depending on the Handwriting setting.")
        if watched.isEmpty {
            Text("No watched folders. Your drop folders are always ingested.")
                .font(Theme.Font.body(11))
                .foregroundStyle(Theme.Colors.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            ForEach($watched) { $folder in
                WatchedRow(folder: $folder) {
                    watched.removeAll { $0.id == folder.id }
                }
            }
        }
        Button(action: pickFolder) {
            HStack(spacing: 4) {
                Image(systemName: "plus").font(.system(size: 9, weight: .semibold))
                Text("Add folder").font(Theme.Font.body(11))
            }
            .foregroundStyle(Theme.Colors.accent)
        }
        .buttonStyle(.plain)
    }

    /// A small, secondary sub-section header inside a settings card, styled
    /// like an iOS grouped-settings section header (smaller and dimmer than a
    /// row label) rather than a row, so it reads as a heading, not a control.
    @ViewBuilder
    private func subhead(_ title: String, help: String? = nil) -> some View {
        HStack(spacing: 5) {
            Text(title)
                .font(Theme.Font.meta(10).weight(.semibold))
                .foregroundStyle(Theme.Colors.textTertiary)
            if let help { HelpButton(text: help) }
            Spacer()
        }
        .padding(.top, 2)
    }

    private var footer: some View {
        HStack(spacing: 12) {
            Spacer()
            Button(action: onClose) {
                Text("Cancel").font(Theme.Font.body(11.5))
                    .foregroundStyle(Theme.Colors.textSecondary)
            }
            .buttonStyle(.plain)

            Button(action: saveAndClose) {
                Text("Save")
                    .font(Theme.Font.body(11.5, weight: .semibold))
                    .foregroundStyle(Theme.Colors.textPrimary)
                    .padding(.horizontal, 14).padding(.vertical, 5)
                    .background(
                        RoundedRectangle(cornerRadius: 7, style: .continuous)
                            .fill(Theme.Colors.accent)
                    )
            }
            .buttonStyle(.plain)
            .disabled(configURL == nil)
            .opacity(configURL == nil ? 0.4 : 1)
        }
    }

    // MARK: - Actions

    private func load() {
        guard let url = ConfigStore.locate(config) else { return }
        configURL = url
        settings = ConfigStore.load(from: url)
        costCap = settings.maxCostPerBuildUSD > 0
            ? String(format: "%g", settings.maxCostPerBuildUSD) : ""
        scheduleHours = settings.scheduleHours
        scheduleEnabled = PipelineRunner.scheduleInstalled
        watched = SourcesStore.load(config)
        loadedKey = EnvStore.readKey(config, keyName: settings.llmProvider.envKeyName)
        apiKey = loadedKey
        for target in ["claude-desktop", "cursor"] {
            connectStatus[target] = PipelineRunner.isMCPConfigured(target) ? .done : .idle
        }
    }

    private func saveAndClose() {
        guard let url = configURL else { onClose(); return }
        settings.scheduleHours = scheduleHours
        settings.maxCostPerBuildUSD = max(0, Double(costCap.trimmingCharacters(in: .whitespaces)) ?? 0)
        ConfigStore.save(settings, to: url)
        SourcesStore.save(watched, config)
        // Only touch .env when the key actually changed, so a failed read
        // can never silently wipe an existing key.
        if apiKey != loadedKey {
            EnvStore.writeKey(apiKey, config, keyName: settings.llmProvider.envKeyName)
        }

        let wasInstalled = PipelineRunner.scheduleInstalled
        let shouldInstall = scheduleEnabled && !scheduleHours.isEmpty
        if let repo = config.repoDir {
            if shouldInstall {
                PipelineRunner.runManaged(repoDir: repo, command: "schedule install")
            } else if wasInstalled {
                PipelineRunner.runManaged(repoDir: repo, command: "schedule uninstall")
            }
        }
        onClose()
    }

    private func connect(_ target: String) {
        guard let repo = config.repoDir else {
            connectStatus[target] = .failed
            return
        }
        connectStatus[target] = .working
        DispatchQueue.global(qos: .userInitiated).async {
            let succeeded = PipelineRunner.runManagedSync(
                repoDir: repo, command: "mcp install --target \(target)"
            )
            DispatchQueue.main.async {
                if succeeded {
                    connectStatus[target] = .done  // stays done: the server is configured
                } else {
                    connectStatus[target] = .failed
                    DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                        if connectStatus[target] == .failed {
                            connectStatus[target] =
                                PipelineRunner.isMCPConfigured(target) ? .done : .idle
                        }
                    }
                }
            }
        }
    }

    private func pickFolder() {
        NSApp.activate(ignoringOtherApps: true)
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.level = .modalPanel
        panel.prompt = "Watch"
        guard panel.runModal() == .OK, let folderURL = panel.url else { return }
        let name = SourcesStore.uniqueName(for: folderURL, existing: watched)
        watched.append(
            WatchedFolder(name: name, path: folderURL.path, enabled: true,
                          file_types: SourcesStore.defaultFileTypes)
        )
    }
}

// MARK: - Building blocks

/// Titled card. The optional "?" beside the title is for groups whose title
/// names a single non-obvious concept (Handwriting, Watched folders); per-
/// field help lives on the `Row` instead.
private struct SettingsGroup<Content: View>: View {
    let title: String
    var help: String? = nil
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            SectionHeader(title: title, help: help)
            VStack(alignment: .leading, spacing: 9) {
                content
            }
            .padding(11)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: Theme.Metric.cornerSmall, style: .continuous)
                    .fill(Theme.Colors.surface.opacity(0.5))
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.Metric.cornerSmall, style: .continuous)
                    .strokeBorder(Theme.Colors.stroke, lineWidth: 1)
            )
        }
    }
}

/// A label (with optional field-level "?") on the left and a trailing control.
private struct Row<Control: View>: View {
    let label: String
    var help: String? = nil
    @ViewBuilder let control: Control

    init(_ label: String, help: String? = nil, @ViewBuilder control: () -> Control) {
        self.label = label
        self.help = help
        self.control = control()
    }

    var body: some View {
        HStack(spacing: 5) {
            Text(label)
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
            if let help { HelpButton(text: help) }
            Spacer(minLength: 8)
            control
        }
    }
}

/// One watched-folder row: name + abbreviated path, an enabled switch, and
/// a remove button.
private struct WatchedRow: View {
    @Binding var folder: WatchedFolder
    let onRemove: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                Text(folder.name)
                    .font(Theme.Font.body(11.5))
                    .foregroundStyle(Theme.Colors.textPrimary)
                    .lineLimit(1).truncationMode(.middle)
                Text(prettyPath(folder.path))
                    .font(Theme.Font.meta(9.5))
                    .foregroundStyle(Theme.Colors.textTertiary)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer(minLength: 6)
            Toggle("", isOn: $folder.enabled)
                .labelsHidden().toggleStyle(.switch).tint(Theme.Colors.accent)
                .scaleEffect(0.85)
            Button(action: onRemove) {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.Colors.textTertiary)
            }
            .buttonStyle(.plain)
            .help("Stop watching this folder")
        }
    }

    private func prettyPath(_ path: String) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return path.hasPrefix(home) ? "~" + path.dropFirst(home.count) : path
    }
}

enum ConnectStatus { case idle, working, done, failed }

/// Small bordered action that reflects the real result of `mcp install`:
/// a spinner while it runs, then a check or an error for a few seconds.
private struct ConnectButton: View {
    let title: String
    let status: ConnectStatus
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                icon
                Text(label).font(Theme.Font.body(10.5))
            }
            .foregroundStyle(tint)
            .padding(.horizontal, 9).padding(.vertical, 4)
            .background(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(Theme.Colors.background)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .strokeBorder(Theme.Colors.stroke, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .disabled(status == .working)
    }

    @ViewBuilder
    private var icon: some View {
        switch status {
        case .working:
            ProgressView().controlSize(.small).scaleEffect(0.6).frame(width: 10, height: 10)
        case .done:
            Image(systemName: "checkmark").font(.system(size: 9, weight: .semibold))
        case .failed:
            Image(systemName: "exclamationmark.triangle").font(.system(size: 9, weight: .semibold))
        case .idle:
            Image(systemName: "link").font(.system(size: 9, weight: .semibold))
        }
    }

    // Connected state is carried by the green check + tint, so the label
    // keeps the client name (both buttons must fit the popover width).
    private var label: String {
        switch status {
        case .working: return "Connecting"
        case .failed:  return "Failed"
        case .done, .idle: return title
        }
    }

    private var tint: Color {
        switch status {
        case .done:   return Theme.Colors.success
        case .failed: return Theme.Colors.danger
        default:      return Theme.Colors.textPrimary
        }
    }
}

/// Editable list of run times (whole hours, 24h). Add / remove rows and
/// pick each hour from a menu -- no free-text parsing.
private struct ScheduleEditor: View {
    @Binding var hours: [Int]

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ForEach(hours, id: \.self) { h in
                HStack(spacing: 8) {
                    Menu {
                        ForEach(0..<24, id: \.self) { candidate in
                            Button(label(candidate)) { change(from: h, to: candidate) }
                        }
                    } label: {
                        HStack(spacing: 5) {
                            Image(systemName: "clock").font(.system(size: 9))
                            Text(label(h)).font(Theme.Font.meta(11.5))
                        }
                        .foregroundStyle(Theme.Colors.textPrimary)
                        .padding(.horizontal, 9).padding(.vertical, 4)
                        .background(
                            RoundedRectangle(cornerRadius: 6, style: .continuous)
                                .fill(Theme.Colors.background)
                        )
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .fixedSize()

                    Spacer(minLength: 6)

                    Button { hours.removeAll { $0 == h } } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 11))
                            .foregroundStyle(Theme.Colors.textTertiary)
                    }
                    .buttonStyle(.plain)
                    .help("Remove this time")
                }
            }

            Button(action: add) {
                HStack(spacing: 4) {
                    Image(systemName: "plus").font(.system(size: 9, weight: .semibold))
                    Text("Add time").font(Theme.Font.body(11))
                }
                .foregroundStyle(Theme.Colors.accent)
            }
            .buttonStyle(.plain)
        }
    }

    private func label(_ h: Int) -> String { String(format: "%02d:00", h) }

    private func change(from old: Int, to new: Int) {
        var set = Set(hours)
        set.remove(old)
        set.insert(new)
        hours = set.sorted()
    }

    private func add() {
        let candidate = (0..<24).first { !hours.contains($0) } ?? 9
        hours = Set(hours).union([candidate]).sorted()
    }
}

/// Compact two-or-more option segmented control in the popover theme.
private struct SegControl<Value: Equatable>: View {
    let options: [(String, Value)]
    @Binding var selection: Value

    var body: some View {
        HStack(spacing: 2) {
            ForEach(options.indices, id: \.self) { i in
                let opt = options[i]
                let selected = opt.1 == selection
                Text(opt.0)
                    .font(Theme.Font.body(11.5, weight: selected ? .semibold : .regular))
                    .foregroundStyle(selected ? Theme.Colors.textPrimary : Theme.Colors.textSecondary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 5)
                    .background(
                        RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .fill(selected ? Theme.Colors.surfaceHover : .clear)
                    )
                    .contentShape(Rectangle())
                    .onTapGesture { selection = opt.1 }
            }
        }
        .padding(3)
        .background(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(Theme.Colors.background)
        )
    }
}
