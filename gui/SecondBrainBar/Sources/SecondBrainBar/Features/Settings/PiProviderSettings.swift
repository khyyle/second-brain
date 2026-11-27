import SwiftUI

/// Choose the provider and manage its saved login. The list loads apart from the rest
/// of Settings, so an unavailable helper doesn't block unrelated edits.
struct PiProviderSettings: View {
    let api: BackendClient
    @Binding var selection: String
    @State private var providers: [PiProviderResponse] = []
    @State private var errorMessage: String?
    @State private var revision = 0
    @State private var loading = true

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            SettingsRow("Provider") {
                Picker("Provider", selection: $selection) {
                    // Keep the saved ID selected while the list loads, or after Pi drops it.
                    // A new vault has none yet.
                    if !providers.contains(where: { $0.id == selection }) {
                        Text(selection.isEmpty ? "Choose provider" : selection).tag(selection)
                    }
                    ForEach(providers) { Text($0.name).tag($0.id) }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .font(Theme.Font.body(11.5))
                .tint(Theme.Colors.textSecondary)
                .disabled(providers.isEmpty)
            }
            if let provider = providers.first(where: { $0.id == selection }) {
                ProviderConnection(api: api, provider: provider).id(provider.id)
            } else if let errorMessage {
                Text(errorMessage).foregroundStyle(Theme.Colors.danger)
                TextAction(title: "Retry providers", icon: "arrow.clockwise") { revision += 1 }
            } else if loading {
                Text("Loading providers…")
            } else if !selection.isEmpty {
                Text("This provider is no longer available. Choose another.")
            }
        }
        .font(Theme.Font.body(11.5)).foregroundStyle(Theme.Colors.textSecondary)
        .task(id: revision) {
            loading = true
            errorMessage = nil
            do { providers = try await api.request("/providers") }
            catch { if !Task.isCancelled { errorMessage = error.localizedDescription } }
            loading = false
        }
    }
}

/// One provider's saved login. Switching providers makes a fresh view, which cancels
/// the previous lookup and any sign-in in progress.
private struct ProviderConnection: View {
    let api: BackendClient
    let provider: PiProviderResponse
    @State private var connected: Bool?
    @State private var loginMethod: String?
    @State private var revision = 0
    @State private var disconnecting = false
    @State private var errorMessage: String?
    @State private var confirmingDisconnect = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let loginMethod {
                ProviderLoginView(api: api, provider: provider.id, method: loginMethod, onFinish: finishLogin)
            } else if let errorMessage {
                Text(errorMessage).foregroundStyle(Theme.Colors.danger)
                TextAction(title: "Retry connection", icon: "arrow.clockwise") { revision += 1 }
            } else if connected == true {
                SettingsRow("Connected") {
                    TextAction(title: "Disconnect", enabled: !disconnecting) { confirmingDisconnect = true }
                }
            } else if connected == nil {
                SettingsRow("Checking connection…") { ProgressView().controlSize(.small) }
            } else if provider.login_methods.isEmpty {
                Text("Sign-in isn’t available for this provider.")
            } else {
                SettingsRow("Connect") {
                    if provider.login_methods.contains("oauth") {
                        Button("Sign in") { loginMethod = "oauth" }.buttonStyle(PillButton(.primary))
                    }
                    if provider.login_methods.contains("api_key") {
                        Button("Use API key") { loginMethod = "api_key" }.buttonStyle(PillButton(.neutral))
                    }
                }
            }
        }
        .task(id: revision) { await checkConnection() }
        .onReceive(NotificationCenter.default.publisher(for: .panelDidHide)) { _ in
            if loginMethod != nil { finishLogin() }
        }
        .confirmationDialog("Disconnect \(provider.name)?", isPresented: $confirmingDisconnect, titleVisibility: .visible) {
            Button("Disconnect", role: .destructive, action: disconnect)
        }
    }

    private func finishLogin() {
        loginMethod = nil
        revision += 1
    }

    private func checkConnection() async {
        struct ConnectionStatus: Decodable { let connected: Bool }
        errorMessage = nil
        connected = nil
        do {
            let status: ConnectionStatus = try await api.request("/providers/\(provider.id)/connection")
            connected = status.connected
        } catch {
            if !Task.isCancelled { errorMessage = error.localizedDescription }
        }
    }

    private func disconnect() {
        disconnecting = true
        Task {
            defer { disconnecting = false }
            do {
                try await api.send("/providers/\(provider.id)/connection", method: "DELETE")
                revision += 1
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
