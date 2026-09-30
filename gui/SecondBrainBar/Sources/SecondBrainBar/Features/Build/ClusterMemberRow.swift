import SwiftUI

/// One chat inside an expanded group. Hovering reveals two actions: pop it
/// out so it builds as an independent source, or remove it from staging. An
/// amber "independent" tag marks a chat already popped out.
struct ClusterMemberRow: View {
    let member: ClusterMember
    let excluded: Bool
    let onToggleExclude: () -> Void
    let onRemove: () -> Void
    let onOpen: () -> Void
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 9) {
            Spacer().frame(width: 16)
            Text(cleanName(member.rel))
                .font(Theme.Font.body(11))
                .foregroundStyle(excluded ? Theme.Colors.textTertiary : Theme.Colors.textSecondary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(member.rel))
            Spacer(minLength: 6)
            if hovering {
                HoverIcon(
                    systemName: excluded ? "arrow.uturn.left.circle" : "arrow.up.right.circle",
                    help: excluded ? "Put back in this group"
                                   : "Build as an independent source instead of in this group",
                    action: onToggleExclude
                )
                .disabled(store.locked)
                HoverIcon(
                    systemName: "xmark.circle.fill",
                    help: "Remove from staging (moves the file to Trash)",
                    action: onRemove
                )
                .disabled(store.locked)
            } else if excluded {
                Text("independent")
                    .font(Theme.Font.meta(9))
                    .foregroundStyle(Theme.Colors.accentAmber)
            }
        }
        .padding(.horizontal, 8).padding(.vertical, 3)
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
    }
}
