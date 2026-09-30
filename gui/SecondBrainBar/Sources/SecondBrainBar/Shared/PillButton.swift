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
