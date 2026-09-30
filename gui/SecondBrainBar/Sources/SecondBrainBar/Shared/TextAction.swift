import SwiftUI

/// Compact text (and optional icon) button whose label brightens to white on hover.
struct TextAction: View {
    let title: String
    var help: String = ""
    var icon: String? = nil
    var restTint: Color = Theme.Colors.textSecondary
    var enabled: Bool = true
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                if let icon {
                    Image(systemName: icon).font(.system(size: 9, weight: .semibold))
                }
                Text(title).font(Theme.Font.body(11))
            }
            .foregroundStyle(color)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .help(help)
        .onHover { hovering = $0 && enabled }
    }

    private var color: Color {
        if !enabled { return Theme.Colors.textTertiary }
        return hovering ? Theme.Colors.textPrimary : restTint
    }
}
