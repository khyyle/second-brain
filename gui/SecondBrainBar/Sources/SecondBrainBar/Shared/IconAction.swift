import SwiftUI

/// Compact icon button whose glyph brightens (to white, or a given tint) on
/// hover. Used in the footer.
struct IconAction: View {
    let systemName: String
    let help: String
    var hoverTint: Color = Theme.Colors.textPrimary
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 12))
                .foregroundStyle(hovering ? hoverTint : Theme.Colors.textSecondary)
        }
        .buttonStyle(.plain)
        .help(help)
        .onHover { hovering = $0 }
    }
}
