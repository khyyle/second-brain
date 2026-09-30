import SwiftUI

/// A borderless icon button whose glyph brightens to white on hover. Row
/// actions use this so hovering a control is clearly highlighted, with no
/// background.
struct HoverIcon: View {
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
