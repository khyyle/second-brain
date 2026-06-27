import SwiftUI

/// Compact capsule button (Keep / Skip / Split, the Build/Stop actions) whose
/// label brightens to white on hover; the capsule fill only deepens on press.
struct PillButton: ButtonStyle {
    /// The intent a pill conveys, mapped to a palette tint so call sites name
    /// the meaning rather than picking a color.
    enum Role {
        case primary
        case confirm
        case destructive
        case warning
        case neutral
        case quiet

        var tint: Color {
            switch self {
            case .primary: return Theme.Colors.textPrimary
            case .confirm: return Theme.Colors.success
            case .destructive: return Theme.Colors.danger
            case .warning: return Theme.Colors.accentAmber
            case .neutral: return Theme.Colors.textSecondary
            case .quiet: return Theme.Colors.textTertiary
            }
        }
    }

    let tint: Color

    init(_ role: Role) { self.tint = role.tint }
    init(tint: Color) { self.tint = tint }

    func makeBody(configuration: Configuration) -> some View {
        PillBody(configuration: configuration, tint: tint)
    }

    private struct PillBody: View {
        let configuration: ButtonStyleConfiguration
        let tint: Color
        @State private var hovering = false

        // Brighten the fill on hover so the cue works for every tint, including
        // the primary pill whose text is already textPrimary (text-only
        // brightening would be invisible there).
        private var fillOpacity: Double {
            if configuration.isPressed { return 0.34 }
            return hovering ? 0.26 : 0.16
        }

        var body: some View {
            configuration.label
                .font(Theme.Font.meta(10).weight(.medium))
                .lineLimit(1)
                .fixedSize()
                .foregroundStyle(hovering ? Theme.Colors.textPrimary : tint)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(Capsule().fill(tint.opacity(fillOpacity)))
                .onHover { hovering = $0 }
        }
    }
}

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

/// Compact text button whose label brightens to white on hover. Used in the
/// footer.
struct TextAction: View {
    let title: String
    let help: String
    var enabled: Bool = true
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(Theme.Font.body(11))
                .foregroundStyle(color)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .help(help)
        .onHover { hovering = $0 && enabled }
    }

    private var color: Color {
        if !enabled { return Theme.Colors.textTertiary }
        return hovering ? Theme.Colors.textPrimary : Theme.Colors.textSecondary
    }
}

/// A small "?" that reveals an explanation in a popover on click. Used beside
/// specific non-obvious fields.
struct HelpButton: View {
    let text: String
    var size: CGFloat = 10
    @State private var show = false
    @State private var hovering = false

    var body: some View {
        Button { show.toggle() } label: {
            Image(systemName: "questionmark.circle")
                .font(.system(size: size))
                .foregroundStyle(hovering ? Theme.Colors.textPrimary : Theme.Colors.textTertiary)
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .popover(isPresented: $show, arrowEdge: .bottom) {
            Text(text)
                .font(Theme.Font.body(12))
                .foregroundStyle(Theme.Colors.textPrimary)
                .frame(width: 260, alignment: .leading)
                .padding(12)
        }
    }
}
