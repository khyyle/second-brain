import SwiftUI
import AppKit

/// In-popover settings panel. Scalar knobs are written straight into
/// `config.yaml`; watched folders go to the GUI-owned `sources.json`;
/// scheduling and MCP wiring shell out to the CLI so the choices take
/// effect. Help "?" buttons sit beside non-obvious fields and groups.
struct SettingsView: View {
    let config: AppConfig
    let onClose: () -> Void

    @State private var settings = PipelineSettings.fallback
    @State private var scheduleHours: [Int] = []
    @State private var scheduleEnabled = false
    @State private var watched: [WatchedFolder] = []
    @State private var connectStatus: [String: ConnectStatus] = [:]
    @State private var connectError: String?
    @State private var apiKey = ""
    @State private var loadedKey = ""
    @State private var costCap = ""
    @State private var configURL: URL?
    @State private var ollamaHealth: PipelineRunner.OllamaHealth?
    @State private var checkingOllama = false

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
            Text("Run the installer again so the app can find its settings.")
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, 8)
    }

    @ViewBuilder
    private var groups: some View {
        SettingsGroup(
            title: "Build",
            help: "The cloud model that writes your wiki pages on a build."
        ) {
            fieldLabel("Provider")
            SegmentedPicker(
                options: LLMProvider.allCases.map { ($0.displayName, $0) },
                selection: providerBinding
            )
            if settings.llmProvider.models.count > 1 {
                SettingsRow("Model") {
                    Picker("", selection: modelBinding) {
                        ForEach(settings.llmProvider.models, id: \.id) { model in
                            Text(model.label).tag(model.id)
                        }
                    }
                    .labelsHidden()
                    .pickerStyle(.menu)
                    .font(Theme.Font.body(11.5))
                    .tint(Theme.Colors.textSecondary)
                }
            }
            fieldLabel("\(settings.llmProvider.displayName) API key")
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
                HStack(spacing: 3) {
                    Image(systemName: "checkmark.circle.fill").font(.system(size: 9))
                    Text("Saved")
                }
                .font(Theme.Font.meta(9.5))
                .foregroundStyle(Theme.Colors.success)
            }
            SettingsRow("Spend cap per build",
                help: "When a build's estimated cost surpasses this limit, "
                    + "wiki compilation will be stopped, rolling back any partially "
                    + "completed pages."
            ) {
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

        SettingsGroup(
            title: "Ollama",
            help: "Local models for triage and semantic search, spanning ingest and "
                + "build. Ollama must be running with its models pulled, or ingest "
                + "and build refuse to run."
        ) {
            ollamaRow
        }

        SettingsGroup(
            title: "Triage",
            help: "Filters imported ChatGPT conversations before building. Other "
                + "sources are not triaged."
        ) {
            SettingsRow("Filter ChatGPT imports") {
                Toggle("", isOn: $settings.triageEnabled)
                    .labelsHidden().toggleStyle(.switch).tint(Theme.Colors.accent)
            }
            SettingsRow("Style",
                help: "How triage determines chats worth keeping. "
                    + "Technical favors conceptual knowledge; project-heavy "
                    + "favors things you're building; skip-heavy keeps only "
                    + "the strongest chats; lenient keeps almost everything.") {
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

        SettingsGroup(
            title: "MCP",
            help: "Install the second-brain MCP, enabling "
                + "agents to search and read your wiki."
        ) {
            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 8) {
                    ConnectButton(title: "Claude Desktop",
                                  status: connectStatus["claude-desktop"] ?? .idle) {
                        connect("claude-desktop")
                    }
                    ConnectButton(title: "ChatGPT",
                                  status: connectStatus["chatgpt-desktop"] ?? .idle) {
                        connect("chatgpt-desktop")
                    }
                    ConnectButton(title: "Cursor",
                                  status: connectStatus["cursor"] ?? .idle) {
                        connect("cursor")
                    }
                    Spacer(minLength: 0)
                }
                if let connectError {
                    Text(connectError)
                        .font(Theme.Font.meta(10))
                        .foregroundStyle(Theme.Colors.danger)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }

        SettingsGroup(title: "Automation") {
            SettingsRow("Run automatically",
                help: "Runs ingest and build in the background. If your computer "
                    + "is asleep background tasks will complete "
                    + "on the next wake.") {
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
                help: "Folders to be ingested, excluding already-processed "
                    + "contents, on a scheduled run.")
        if watched.isEmpty {
            Text("No watched folders.")
                .font(Theme.Font.body(11))
                .foregroundStyle(Theme.Colors.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            ForEach($watched) { $folder in
                WatchedFolderRow(folder: $folder) {
                    watched.removeAll { $0.id == folder.id }
                }
            }
        }
        TextAction(title: "Add folder", icon: "plus", restTint: Theme.Colors.accent, action: pickFolder)
    }

    /// Caption above a full-width control (segmented control, text field).
    /// Distinct from `subhead` (a sub-section divider) and `SettingsRow` (label
    /// with a trailing control), so the three tiers stay visually consistent.
    @ViewBuilder
    private func fieldLabel(_ text: String) -> some View {
        Text(text)
            .font(Theme.Font.meta(10))
            .foregroundStyle(Theme.Colors.textSecondary)
    }

    @ViewBuilder
    private var ollamaRow: some View {
        SettingsRow("Status") {
            let (text, color): (String, Color) = {
                if checkingOllama { return ("Checking…", Theme.Colors.textTertiary) }
                guard let health = ollamaHealth else {
                    return ("Unknown", Theme.Colors.textTertiary)
                }
                if health.healthy { return ("Ready", Theme.Colors.success) }
                if !health.reachable { return ("Not running", Theme.Colors.danger) }
                return ("Models missing", Theme.Colors.accentAmber)
            }()
            HStack(spacing: 8) {
                Button(action: probeOllama) {
                    Text("Re-check")
                        .font(Theme.Font.body(10.5))
                        .foregroundStyle(Theme.Colors.textPrimary)
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
                .disabled(checkingOllama || config.repoDir == nil)

                Text(text)
                    .font(Theme.Font.meta(10))
                    .foregroundStyle(color)
            }
        }
        if let health = ollamaHealth, !health.healthy {
            Text(health.message)
                .font(Theme.Font.meta(10))
                .foregroundStyle(Theme.Colors.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
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
        for target in ["claude-desktop", "chatgpt-desktop", "cursor"] {
            connectStatus[target] = PipelineRunner.isMCPConfigured(target) ? .done : .idle
        }
        probeOllama()
    }

    private func probeOllama() {
        guard let repo = config.repoDir, !checkingOllama else { return }
        checkingOllama = true
        DispatchQueue.global(qos: .userInitiated).async {
            let health = PipelineRunner.checkOllama(repoDir: repo)
            DispatchQueue.main.async {
                ollamaHealth = health
                checkingOllama = false
            }
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
        connectError = nil
        DispatchQueue.global(qos: .userInitiated).async {
            let commandResult = PipelineRunner.runManagedResult(
                repoDir: repo, command: "mcp install --target \(target)"
            )
            DispatchQueue.main.async {
                if commandResult.succeeded {
                    connectStatus[target] = .done  // stays done: the server is configured
                } else {
                    connectStatus[target] = .failed
                    connectError = commandResult.message ?? "Could not configure the MCP server."
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
