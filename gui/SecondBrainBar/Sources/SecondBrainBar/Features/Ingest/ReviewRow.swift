import SwiftUI

struct ReviewRow: View {
    let row: TriageRow
    let onOpen: () -> Void
    let onKeep: () -> Void
    let onSkip: () -> Void
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: "questionmark.circle")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Theme.Colors.accentAmber)
                .frame(width: 12)
            Text(cleanName(row.displayName))
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(row.displayName))
            Spacer(minLength: 6)
            Button("Keep", action: onKeep)
                .buttonStyle(PillButton(.confirm))
                .disabled(store.locked)
            Button("Skip", action: onSkip)
                .buttonStyle(PillButton(.quiet))
                .disabled(store.locked)
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
    }
}
