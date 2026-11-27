import SwiftUI

/// The model the next build runs, chosen from what the provider connected in Settings offers.
struct BuildModelMenu: View {
    let onOpenSettings: () -> Void
    @EnvironmentObject private var store: PipelineStore
    @State private var hovering = false

    var body: some View {
        Group {
            switch store.buildModels {
            case .loading:
                // Hold the saved choice in place until its display name arrives.
                Text(store.compilationModel)
                    .font(Theme.Font.body(11))
                    .foregroundStyle(Theme.Colors.textTertiary)
                    .lineLimit(1).truncationMode(.middle)
            case .disconnected:
                TextAction(title: "Connect provider", help: "Open Settings to connect a model provider",
                           restTint: Theme.Colors.accent, action: onOpenSettings)
            case .failed(let reason):
                TextAction(title: "Retry models", help: reason, icon: "arrow.clockwise", action: reload)
            case .available(let models):
                menu(models)
            }
        }
        .task(id: store.compilationProvider) { await store.refreshBuildModels() }
        .onPanelShow(reload)
    }

    private func reload() { Task { await store.refreshBuildModels() } }

    private func menu(_ models: [ModelChoiceResponse]) -> some View {
        let saved = store.compilationModel
        let chosen = models.first { $0.id == saved }
        let title = chosen?.name ?? (models.isEmpty ? "No models available" : saved.isEmpty ? "Choose model" : "\(saved) unavailable")
        // A build can't start without a choice, so the missing one stands out.
        let tint = chosen != nil ? Theme.Colors.textSecondary : models.isEmpty ? Theme.Colors.textTertiary : Theme.Colors.accent
        return Menu {
            ForEach(models) { model in
                // A toggle is how a menu shows the current item with a checkmark.
                Toggle(model.name, isOn: Binding(get: { model.id == saved }, set: { _ in store.selectBuildModel(model.id) }))
            }
            Divider()
            Button("Change provider…", action: onOpenSettings)
        } label: {
            HStack(spacing: 3) {
                Text(title).lineLimit(1).truncationMode(.middle)
                Image(systemName: "chevron.up.chevron.down").font(.system(size: 7, weight: .semibold))
            }
            .font(Theme.Font.body(11))
            .foregroundStyle(hovering ? Theme.Colors.textPrimary : tint)
            .padding(.vertical, 5)
            .contentShape(Rectangle())
        }
        .padding(.vertical, -5) // A taller click target than the text, without moving the row.
        // A plain button menu draws this label as written; the borderless style substitutes AppKit's.
        .menuStyle(.button)
        .buttonStyle(.plain)
        .menuIndicator(.hidden)
        .fixedSize(horizontal: false, vertical: true)
        .onHover { hovering = $0 }
        .disabled(store.locked) // A running build keeps the model it started with.
        .help("Model for the next build")
        .accessibilityLabel("Model: \(title)")
    }
}
