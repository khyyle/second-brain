import SwiftUI

struct BuildRow: View {
    let entry: BuildLogEntry
    let onOpen: () -> Void
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: entry.action == .created ? "plus.circle" : "pencil")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(entry.action == .created ? Theme.Colors.success : Theme.Colors.textSecondary)
                .frame(width: 12)
            Text(verb)
                .font(Theme.Font.meta(10))
                .foregroundStyle(Theme.Colors.textTertiary)
            Text(cleanName(entry.pageName))
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(entry.pageName))
            Spacer(minLength: 6)
            Text(relativeTime(entry.at))
                .font(Theme.Font.meta(10.5))
                .foregroundStyle(Theme.Colors.textTertiary)
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
    }

    private var verb: String { entry.action == .created ? "created" : "updated" }
}
