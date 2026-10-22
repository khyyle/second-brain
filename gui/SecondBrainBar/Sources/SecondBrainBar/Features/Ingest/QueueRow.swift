import SwiftUI

struct QueueRow: View {
    let item: QueueItem
    var failureReason: String? = nil
    let onOpen: () -> Void
    var onRemove: (() -> Void)? = nil
    var onRetry: (() -> Void)? = nil
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 9) {
            Group {
                if item.state.isWorking {
                    RowSpinner()
                } else if item.state == .finished {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Theme.Colors.success)
                        .transition(.scale.combined(with: .opacity))
                } else {
                    Image(systemName: stateIcon)
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(stateColor)
                }
            }
            .frame(width: 12)
            .animation(.easeOut(duration: 0.2), value: item.state)
            Text(cleanName(item.displayName))
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(cleanName(item.displayName))
            if item.state == .failed, let failureReason {
                ReasonBadge(
                    label: "Failed",
                    tint: Theme.Colors.danger,
                    note: "This file couldn't be parsed: \(failureReason)",
                    accessibilityHint: "Shows why this file failed"
                )
            }
            Spacer(minLength: 6)
            if item.state == .failed, let onRetry {
                Button("Retry", action: onRetry)
                    .buttonStyle(PillButton(.warning))
                    .disabled(store.locked)
            }
            TrailingReserve(hovering: hovering && onRemove != nil) {
                if item.state.isWorking {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        Text(formatElapsed(max(0, context.date.timeIntervalSince(item.since))))
                            .font(Theme.Font.meta(10))
                            .foregroundStyle(Theme.Colors.textTertiary)
                            .monospacedDigit()
                    }
                } else if item.state == .finished {
                    Text(item.outcome?.rawValue ?? item.state.label)
                        .font(Theme.Font.meta(10))
                        .foregroundStyle(Theme.Colors.textTertiary)
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
                if let onRemove {
                    HoverIcon(systemName: "xmark.circle.fill",
                              help: "Remove (moves the file to Trash)",
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

    private var stateIcon: String {
        switch item.state {
        case .failed:    return "exclamationmark.triangle"
        case .duplicate: return "checkmark.circle.fill"
        default:         return "clock"
        }
    }

    private var stateColor: Color {
        switch item.state {
        case .failed: return Theme.Colors.danger
        case .duplicate: return Theme.Colors.accentAmber
        default: return Theme.Colors.textTertiary
        }
    }
}
