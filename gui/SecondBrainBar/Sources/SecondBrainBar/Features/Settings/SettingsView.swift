import SwiftUI
import AppKit

/// API-backed preferences and integrations; provider secrets are stored in Keychain.
struct SettingsView: View {
    let config: AppConfig
    let onClose: () -> Void

    @State private var catalog: ApplicationCatalog?
    @State private var provider = ""
    @State private var model = ""
    @State private var triageEnabled = true
    @State private var triageProfile = ""
    @State private var costCap = ""
    @State private var apiKeys: [String: String] = [:]
    @State private var configured: [String: Bool] = [:]
    @State private var watched: [WatchedFolder] = []
    @State private var connectStatus: [String: ConnectStatus] = [:]
    @State private var connectError: String?
    @State private var ollamaHealth: OllamaHealth?
    @State private var checkingOllama = false
    @State private var loaded = false
    @State private var saving = false
    @State private var errorMessage: String?
    private var api: BackendClient { BackendClient(config: config) }
    private var selectedProvider: ProviderInfo? { catalog?.providers.first { $0.id == provider } }
    private var keyBinding: Binding<String> {
        Binding(get: { apiKeys[provider] ?? "" }, set: { apiKeys[provider] = $0 })
    }
    private var providerBinding: Binding<String> {
        Binding(get: { provider }, set: { value in
            provider = value
            model = catalog?.providers.first { $0.id == value }?.models.first?.id ?? ""
        })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if !loaded {
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
        .contentShape(Rectangle())
        .onTapGesture { NSApp.keyWindow?.makeFirstResponder(nil) }
        .onAppear(perform: load)
        .disabled(saving)
        .alert("Couldn't update settings", isPresented: Binding(
            get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } }
        )) { Button("OK", role: .cancel) {} } message: { Text(errorMessage ?? "") }
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
            Text("Loading settings…")
                .font(Theme.Font.body(12, weight: .semibold))
                .foregroundStyle(Theme.Colors.textPrimary)
            Text("Settings are loaded from the local backend.")
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
                options: (catalog?.providers ?? []).map { ($0.name, $0.id) },
                selection: providerBinding
            )
            if (selectedProvider?.models.count ?? 0) > 1 {
                SettingsRow("Model") {
                    Picker("", selection: $model) {
                        ForEach(selectedProvider?.models ?? [], id: \.id) { model in
                            Text(model.id).tag(model.id)
                        }
                    }
                    .labelsHidden()
                    .pickerStyle(.menu)
                    .font(Theme.Font.body(11.5))
                    .tint(Theme.Colors.textSecondary)
                }
            }
            FloatingLabelSecureField(
                title: "\(selectedProvider?.name ?? provider) API key",
                text: keyBinding
            )
            .id(provider)
            .help("Leave blank to keep the saved key.")
            if configured[provider] == true {
                HStack(spacing: 3) {
                    Image(systemName: "checkmark.circle.fill").font(.system(size: 9))
                    Text("Saved in Keychain")
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
                Toggle("", isOn: $triageEnabled)
                    .labelsHidden().toggleStyle(.switch).tint(Theme.Colors.accent)
            }
            SettingsRow("Style",
                help: "How triage determines chats worth keeping. "
                    + "Technical favors conceptual knowledge; project-heavy "
                    + "favors things you're building; skip-heavy keeps only "
                    + "the strongest chats; lenient keeps almost everything.") {
                Picker("", selection: $triageProfile) {
                    ForEach(catalog?.triage_profiles ?? [], id: \.self) { p in
                        Text(p.replacingOccurrences(of: "_", with: " ").capitalized).tag(p)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .font(Theme.Font.body(11.5))
                .tint(Theme.Colors.textSecondary)
                .disabled(!triageEnabled)
                .opacity(triageEnabled ? 1 : 0.4)
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
            watchedFolders
        }
    }

    @ViewBuilder
    private var watchedFolders: some View {
        subhead("Watched folders",
                help: "New and changed files are copied in and ingested automatically "
                    + "while the backend is running. Originals stay in place. "
                    + "Build the wiki manually when you are ready.")
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
                .disabled(checkingOllama)

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
            .disabled(!loaded || saving)
            .opacity(!loaded ? 0.4 : 1)
        }
    }

    private func load() {
        Task {
            do {
                async let fetchedCatalog: ApplicationCatalog = api.request("/catalog")
                async let preferences: PreferencesResponse = api.request("/settings")
                async let folders: [WatchedFolder] = api.request("/watched-folders")
                let (c, p, f) = try await (fetchedCatalog, preferences, folders)
                catalog = c
                provider = p.compilation.provider
                model = p.compilation.model
                costCap = p.compilation.max_cost_per_build_usd > 0 ? String(p.compilation.max_cost_per_build_usd) : ""
                triageEnabled = p.triage.enabled
                triageProfile = p.triage.profile
                watched = f
                loaded = true
                for provider in c.providers {
                    let status: CredentialStatus = try await api.request("/credentials/\(provider.id)")
                    configured[provider.id] = status.configured
                }
                for target in ["claude-desktop", "chatgpt-desktop", "cursor"] {
                    let status: IntegrationStatus = try await api.request("/integrations/mcp/\(target)")
                    connectStatus[target] = status.configured ? .done : .idle
                }
                probeOllama()
            } catch { errorMessage = error.localizedDescription }
        }
    }

    private func probeOllama() {
        guard !checkingOllama else { return }
        checkingOllama = true
        Task {
            defer { checkingOllama = false }
            do { ollamaHealth = try await api.request("/diagnostics") }
            catch { errorMessage = error.localizedDescription }
        }
    }

    private func saveAndClose() {
        let text = costCap.trimmingCharacters(in: .whitespaces)
        guard text.isEmpty || (Double(text).map { $0.isFinite && $0 >= 0 } ?? false) else {
            errorMessage = "Enter a nonnegative spend cap, or leave it blank for no cap."
            return
        }
        saving = true
        Task {
            defer { saving = false }
            do {
                let sources = Dictionary(uniqueKeysWithValues: watched.map { folder -> (String, [String: Any]) in
                    var value: [String: Any] = ["path": folder.path, "enabled": folder.enabled, "file_types": folder.file_types]
                    if let lane = folder.force_parse_lane { value["force_parse_lane"] = lane }
                    return (folder.name, value)
                })
                // One validated settings write preserves unrelated preferences and saves folders atomically.
                try await api.send("/settings", method: "PATCH", body: [
                    "compilation": ["provider": provider, "model": model, "max_cost_per_build_usd": Double(text) ?? 0],
                    "triage": ["enabled": triageEnabled, "profile": triageProfile], "sources": sources
                ])
                for (provider, key) in apiKeys where !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    try ProviderKeychain.save(key, provider: provider)
                }
                onClose()
            } catch { errorMessage = error.localizedDescription }
        }
    }

    private func connect(_ target: String) {
        connectStatus[target] = .working
        connectError = nil
        Task {
            do {
                let status: IntegrationStatus = try await api.request("/integrations/mcp/\(target)", method: "PUT")
                connectStatus[target] = status.configured ? .done : .failed
                if !status.configured { connectError = status.message }
            } catch {
                connectStatus[target] = .failed
                connectError = error.localizedDescription
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
        let name = WatchedFolderNames.uniqueName(for: folderURL, existing: watched)
        watched.append(
            WatchedFolder(name: name, path: folderURL.path, enabled: true,
                          file_types: (catalog?.file_types ?? []).filter { $0 != "json" })
        )
    }
}
