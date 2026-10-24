import SwiftUI

/// Shared presentation for request errors and completed job outcomes.
struct NoticeBanner: View {
    let headline: String
    let reason: String
    var symbolName = "xmark.octagon.fill"
    var tint = Theme.Colors.danger
    let onDismiss: () -> Void
    @State private var hoveringDismiss = false

    private static let dismissHitSize: CGFloat = 28

    var body: some View {
        HStack(spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: symbolName)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(tint)
                VStack(alignment: .leading, spacing: 1) {
                    Text(headline)
                        .font(Theme.Font.body(11.5, weight: .semibold))
                        .foregroundStyle(Theme.Colors.textPrimary)
                    if !reason.isEmpty {
                        Text(reason)
                            .font(Theme.Font.body(10.5))
                            .foregroundStyle(Theme.Colors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 0)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel(reason.isEmpty ? headline : "\(headline): \(reason)")

            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(
                        hoveringDismiss ? Theme.Colors.textPrimary : Theme.Colors.textSecondary
                    )
                    .frame(width: Self.dismissHitSize, height: Self.dismissHitSize)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Dismiss")
            .accessibilityLabel("Dismiss")
            .onHover { hoveringDismiss = $0 }
        }
        .padding(.leading, 10)
        .padding(.vertical, 4)
        .background(
            RoundedRectangle(cornerRadius: Theme.Metric.cornerSmall, style: .continuous)
                .fill(tint.opacity(0.10))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Metric.cornerSmall, style: .continuous)
                .strokeBorder(tint.opacity(0.35), lineWidth: 1)
        )
    }
}
