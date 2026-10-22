import SwiftUI

/// One source waiting to be compiled; hover to un-ingest it.
struct StagedRow: View {
    let name: String
    let costText: String
    var deferReason: String? = nil
    var onRetry: (() -> Void)? = nil
    var isChat: Bool = false
    var onSkip: (() -> Void)? = nil
    var isBuilding: Bool = false
    let onOpen: () -> Void
    let onRemove: () -> Void
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 9) {
            // spinner and dashed circle share a 12pt slot so filenames dont shift on build
            Group {
                if isBuilding {
                    RowSpinner()
                } else {
                    Image(systemName: "circle.dashed")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Theme.Colors.accentAmber)
                }
            }
            .frame(width: 12)
            VStack(alignment: .leading, spacing: 1) {
                Text(cleanName(name))
                    .font(Theme.Font.body(11.5))
                    .foregroundStyle(Theme.Colors.textPrimary)
                    .lineLimit(1).truncationMode(.middle)
                    .openableTitle(cleanName(name))
                if isBuilding {
                    BuildActivityLine()
                }
            }
            if isChat {
                Text("chat")
                    .font(Theme.Font.meta(9.5))
                    .foregroundStyle(Theme.Colors.textTertiary)
            }
            if let deferReason {
                ReasonBadge(
                    label: "Set aside",
                    tint: Theme.Colors.accentAmber,
                    note: "Builds will skip this source: \(deferReason)",
                    accessibilityHint: "Shows why builds will skip this source"
                )
            }
            Spacer(minLength: 6)
            TrailingReserve(hovering: hovering) {
                if isBuilding {
                    BuildElapsed()
                } else {
                    Text(costText)
                        .font(Theme.Font.meta(9.5))
                        .foregroundStyle(Theme.Colors.textTertiary)
                }
            } hover: {
                HStack(spacing: 4) {
                    if let onRetry {
                        HoverIcon(systemName: "arrow.clockwise",
                                  help: "Try again on the next build",
                                  action: onRetry)
                            .disabled(store.locked)
                    }
                    if let onSkip {
                        HoverIcon(systemName: "minus.circle",
                                  help: "Skip this chat",
                                  action: onSkip)
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
}
