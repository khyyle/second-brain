import SwiftUI

struct QueueRow: View {
    let item: QueueItem
    let onOpen: () -> Void
    let onRemove: () -> Void
    var onRetry: (() -> Void)? = nil
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 9) {
            Group {
                if item.state == .processing {
                    ProgressView().controlSize(.small).scaleEffect(0.6)
                } else {
                    Image(systemName: stateIcon)
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(item.state == .failed ? Theme.Colors.danger : Theme.Colors.textTertiary)
                }
            }
            .frame(width: 12)
            Text(cleanName(item.displayName))
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(item.displayName))
            Spacer(minLength: 6)
            if item.state == .failed, let onRetry {
                Button("Retry", action: onRetry)
                    .buttonStyle(PillButton(.warning))
                    .disabled(store.locked)
            }
            TrailingReserve(hovering: hovering) {
                if item.state == .processing {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        Text(formatElapsed(max(0, context.date.timeIntervalSince(item.since))))
                            .font(Theme.Font.meta(10))
                            .foregroundStyle(Theme.Colors.textTertiary)
                            .monospacedDigit()
                    }
                } else if item.state == .waiting || item.state == .duplicate {
                    Text(item.state.label)
                        .font(Theme.Font.meta(10))
                        .foregroundStyle(Theme.Colors.textTertiary)
                        .monospacedDigit()
                        .help(item.state == .duplicate
                            ? "This file's content is already in Second Brain"
                            : "")
                }
            } hover: {
                HoverIcon(systemName: "xmark.circle.fill",
                          help: "Remove (moves the file to Trash)",
                          action: onRemove)
                    .disabled(store.locked)
            }
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
    }

    private var stateIcon: String {
        switch item.state {
        case .failed:    return "exclamationmark.triangle"
        case .duplicate: return "checkmark.circle"
        default:         return "clock"
        }
    }
}
