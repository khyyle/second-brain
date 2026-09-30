import SwiftUI

struct TriageDecidedRow: View {
    let row: TriageRow
    let onOpen: () -> Void
    let onSkip: () -> Void
    let onUnskip: () -> Void
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 7) {
            Text(cleanName(row.displayName))
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(row.displayName))
            Spacer(minLength: 6)
            // The action fades in beside the badge (kept always present so the
            // status never appears to flip and the row doesn't reflow).
            hoverAction
                .opacity(hovering ? 1 : 0)
                .allowsHitTesting(hovering)
                .disabled(store.locked)
            badge
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
        .animation(.easeInOut(duration: 0.12), value: hovering)
    }

    @ViewBuilder
    private var hoverAction: some View {
        switch row.decision {
        case .worthwhile:
            HoverIcon(systemName: "minus.circle", help: "Skip this chat",
                      size: 12, action: onSkip)
        case .skip:
            HoverIcon(systemName: "plus.circle", help: "Keep this chat",
                      size: 12, restTint: Theme.Colors.success, action: onUnskip)
        case .review:
            EmptyView()
        }
    }

    private var badge: some View {
        Text(row.decision.label)
            .font(Theme.Font.meta(9.5).weight(.medium))
            .foregroundStyle(badgeColor)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(Capsule().fill(badgeColor.opacity(0.14)))
    }

    private var badgeColor: Color {
        switch row.decision {
        case .worthwhile: return Theme.Colors.success
        case .review:     return Theme.Colors.accentAmber
        case .skip:       return Theme.Colors.textTertiary
        }
    }
}
