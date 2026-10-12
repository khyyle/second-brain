import SwiftUI

/// One row under the tab bar for a run that didn't fully succeed. Clicking it
/// opens the stage's tab; it stays until dismissed or the next run of that
/// stage replaces it.
struct RunOutcomeBanner: View {
    let notice: RunNotice
    let tabName: String
    let onOpen: () -> Void
    let onDismiss: () -> Void
    @State private var hovering = false

    private static let dismissHitSize: CGFloat = 28

    var body: some View {
        HStack(spacing: 4) {
            Button(action: onOpen) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: notice.symbolName)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(notice.tint)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(notice.headline)
                            .font(Theme.Font.body(11.5, weight: .semibold))
                            .foregroundStyle(Theme.Colors.textPrimary)
                        if !notice.run.reason.isEmpty {
                            Text(notice.run.reason)
                                .font(Theme.Font.body(10.5))
                                .foregroundStyle(Theme.Colors.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Open the \(tabName) tab")
            .accessibilityLabel(notice.accessibilityText)
            .accessibilityHint("Opens the \(tabName) tab")

            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(hovering ? Theme.Colors.textPrimary : Theme.Colors.textSecondary)
                    .frame(width: Self.dismissHitSize, height: Self.dismissHitSize)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Dismiss")
            .accessibilityLabel("Dismiss")
        }
        .padding(.leading, 10)
        .padding(.vertical, 4)
        .background(
            RoundedRectangle(cornerRadius: Theme.Metric.cornerSmall, style: .continuous)
                .fill(notice.tint.opacity(hovering ? 0.16 : 0.10))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Metric.cornerSmall, style: .continuous)
                .strokeBorder(notice.tint.opacity(0.35), lineWidth: 1)
        )
        .onHover { hovering = $0 }
    }
}

extension RunNotice {
    var headline: String {
        let stageName = stage == .compile ? "Build" : "Ingest"
        switch run.outcome {
        case .failed: return "\(stageName) failed"
        case .capped: return "\(stageName) stopped early"
        case .partial, .ok, .stopped: return "\(stageName) incomplete"
        }
    }

    /// Failure and partial endings differ in shape, not only color.
    var symbolName: String {
        run.outcome == .failed ? "xmark.octagon.fill" : "exclamationmark.triangle.fill"
    }

    var tint: Color {
        run.outcome == .failed ? Theme.Colors.danger : Theme.Colors.accentAmber
    }

    var accessibilityText: String {
        run.reason.isEmpty ? headline : "\(headline): \(run.reason)"
    }
}
