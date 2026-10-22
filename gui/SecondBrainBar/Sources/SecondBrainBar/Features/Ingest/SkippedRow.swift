import SwiftUI

/// One skipped chat that can be restored to the build queue.
struct SkippedRow: View {
    let row: TriageRow
    let onOpen: () -> Void
    let onKeep: () -> Void
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
            HoverIcon(systemName: "plus.circle", help: "Keep this chat",
                      size: 12, restTint: Theme.Colors.success, action: onKeep)
                .opacity(hovering ? 1 : 0)
                .allowsHitTesting(hovering)
                .disabled(store.locked)
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
        .animation(.easeInOut(duration: 0.12), value: hovering)
    }
}
