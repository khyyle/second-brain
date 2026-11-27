import SwiftUI

@MainActor
struct ProviderLoginView: View {
    let provider: String
    let method: String
    let onFinish: () -> Void
    @StateObject private var session: ProviderLoginSession
    @State private var revealedPromptID: Int?
    @Environment(\.openURL) private var openURL

    init(api: BackendClient, provider: String, method: String, onFinish: @escaping () -> Void) {
        _session = StateObject(wrappedValue: ProviderLoginSession(api: api))
        self.provider = provider
        self.method = method
        self.onFinish = onFinish
    }

    var body: some View {
        let display = session.display
        let fallback = display.prompt?.kind == .manualCode && display.prompt?.callback_listening == true
            && usesLocalCallback(display.browserURL)
        VStack(alignment: .leading, spacing: 8) {
            if !fallback, let instructions = display.instructions { Text(verbatim: instructions) }
            if let url = display.browserURL { browserLink(url, title: "Open sign-in page") }
            if let device = display.device {
                SettingsRow("Device code") {
                    Text(verbatim: device.code).font(Theme.Font.meta(12)).textSelection(.enabled)
                }
                if device.verificationURL != display.browserURL { browserLink(device.verificationURL, title: "Verify device") }
            }
            if let info = display.info { Text(verbatim: info) }
            ForEach(display.links.indices, id: \.self) { index in
                let link = display.links[index]
                browserLink(link.url, title: link.label ?? link.url.host ?? "Learn more")
            }
            if let message = display.message { Text(verbatim: message) }
            if display.phase == .cancellationUnconfirmed {
                HStack {
                    TextAction(title: "Retry cancellation", action: session.cancel)
                    TextAction(title: "Close", action: onFinish)
                }
            } else if display.phase == .closed {
                TextAction(title: "Close", action: onFinish)
            } else if let prompt = display.prompt, !fallback || revealedPromptID == prompt.id {
                LoginPromptField(prompt: prompt, label: fallback ? "Redirect URL" : prompt.message, cancel: session.cancel) {
                    session.submit(promptID: prompt.id, answer: $0)
                }
                .id(prompt.id)
            } else if display.phase == .signingIn {
                HStack {
                    if fallback, let prompt = display.prompt {
                        TextAction(title: "Paste redirect URL instead", icon: "chevron.right") { revealedPromptID = prompt.id }
                        Spacer()
                    }
                    TextAction(title: "Cancel sign-in", action: session.cancel)
                }
            }
        }
        .font(Theme.Font.body(11.5)).foregroundStyle(Theme.Colors.textSecondary)
        .fixedSize(horizontal: false, vertical: true)
        .privacySensitive()
        .onAppear { session.start(provider: provider, method: method, onFinish: onFinish) }
        .onDisappear { session.abandon() }
        .onReceive(NotificationCenter.default.publisher(for: .panelDidHide)) { _ in session.abandon() }
    }

    // Pi also emits manual_code for headless logins, where pasting is required.
    private func usesLocalCallback(_ url: URL?) -> Bool {
        guard let url, let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems else { return false }
        return items.contains { item in
            guard item.name == "redirect_uri" || item.name == "callback_url",
                  let value = item.value, let host = URL(string: value)?.host?.lowercased() else { return false }
            return ["localhost", "127.0.0.1", "[::1]"].contains(host)
        }
    }

    private func browserLink(_ url: URL, title: String) -> some View {
        TextAction(title: title, help: url.host ?? "", icon: "arrow.up.right", restTint: Theme.Colors.accent) { openURL(url) }
    }

}

private struct LoginPromptField: View {
    let prompt: LoginPrompt
    let label: String
    let cancel: () -> Void
    let submit: (String) -> Void
    @State private var answer = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if prompt.kind != .secret { Text(verbatim: label).foregroundStyle(Theme.Colors.textPrimary) }
            if prompt.kind == .select {
                HStack {
                    Menu("Choose an option") {
                        ForEach(prompt.options ?? []) { option in
                            Button(option.label) { submit(option.id) }
                                .help(option.description ?? option.label)
                        }
                    }
                    .accessibilityLabel(prompt.message)
                    TextAction(title: "Cancel sign-in", action: cancel)
                }
            } else {
                if prompt.kind == .secret {
                    FloatingLabelSecureField(title: prompt.message, text: $answer)
                        .help(prompt.placeholder ?? prompt.message).onSubmit(send)
                } else {
                    Group {
                        if prompt.kind == .text { TextField(prompt.placeholder ?? "", text: $answer) }
                        else { SecureField(prompt.placeholder ?? "", text: $answer) }
                    }
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel(label)
                    .focused($focused).onAppear { focused = true }
                    .onSubmit(send)
                }
                HStack {
                    TextAction(title: "Cancel sign-in", action: cancel)
                    Spacer()
                    Button("Continue", action: send)
                        .buttonStyle(PillButton(.primary)).disabled(!prompt.accepts(answer))
                        .opacity(prompt.accepts(answer) ? 1 : 0.4)
                }
            }
        }
        .controlSize(.small)
    }

    private func send() {
        guard prompt.accepts(answer) else { return }
        submit(answer)
        answer = ""
    }
}
