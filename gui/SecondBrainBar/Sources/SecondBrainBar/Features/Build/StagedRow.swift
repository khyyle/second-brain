import SwiftUI

/// One source waiting to be compiled; hover to un-ingest it.
struct StagedRow: View {
    let name: String
    let sizeText: String
    var deferReason: String? = nil
    var onRetry: (() -> Void)? = nil
    let onOpen: () -> Void
    let onRemove: () -> Void
    @State private var hovering = false
    @State private var badgeHovering = false
    @State private var showingDeferReason = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: "circle.dashed")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Theme.Colors.accentAmber)
                .frame(width: 12)
            Text(cleanName(name))
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(name))
            if let deferReason {
                deferBadge(deferReason)
            }
            Spacer(minLength: 6)
            TrailingReserve(hovering: hovering) {
                Text(sizeText)
                    .font(Theme.Font.meta(9.5))
                    .foregroundStyle(Theme.Colors.textTertiary)
            } hover: {
                HStack(spacing: 4) {
                    if let onRetry {
                        HoverIcon(systemName: "arrow.clockwise",
                                  help: "Try again on the next build",
                                  action: onRetry)
                            .disabled(store.locked)
                    }
                    HoverIcon(systemName: "xmark.circle.fill",
                              help: "Remove source (moves the raw file to Trash)",
                              action: onRemove)
                        .disabled(store.locked)
                }
            }
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
    }

    /// Opens the reason on click rather than behind a delayed hover tooltip.
    private func deferBadge(_ reason: String) -> some View {
        Button { showingDeferReason.toggle() } label: {
            Text("Set aside")
                .font(Theme.Font.meta(9.5).weight(.medium))
                .foregroundStyle(Theme.Colors.accentAmber)
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(
                    Capsule().fill(Theme.Colors.accentAmber.opacity(badgeHovering ? 0.24 : 0.14))
                )
        }
        .buttonStyle(.plain)
        .onHover { badgeHovering = $0 }
        .accessibilityHint("Shows why builds will skip this source")
        .popover(isPresented: $showingDeferReason, arrowEdge: .bottom) {
            PopoverNote(text: "Builds will skip this source: \(reason)")
        }
    }
}
