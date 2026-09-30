import SwiftUI

enum ConnectStatus { case idle, working, done, failed }

/// Small bordered action that reflects the real result of `mcp install`:
/// a spinner while it runs, then a check or an error for a few seconds.
struct ConnectButton: View {
    let title: String
    let status: ConnectStatus
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                icon
                Text(label).font(Theme.Font.body(10.5))
            }
            .foregroundStyle(tint)
            .padding(.horizontal, 9).padding(.vertical, 4)
            .background(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(Theme.Colors.background)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .strokeBorder(Theme.Colors.stroke, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .disabled(status == .working)
    }

    @ViewBuilder
    private var icon: some View {
        switch status {
        case .working:
            ProgressView().controlSize(.small).scaleEffect(0.6).frame(width: 10, height: 10)
        case .done:
            Image(systemName: "checkmark").font(.system(size: 9, weight: .semibold))
        case .failed:
            Image(systemName: "exclamationmark.triangle").font(.system(size: 9, weight: .semibold))
        case .idle:
            Image(systemName: "link").font(.system(size: 9, weight: .semibold))
        }
    }

    // Connected state is carried by the green check and tint, leaving the
    // short label available to identify the client.
    private var label: String {
        switch status {
        case .working: return "Connecting"
        case .failed:  return "Failed"
        case .done, .idle: return title
        }
    }

    private var tint: Color {
        switch status {
        case .done:   return Theme.Colors.success
        case .failed: return Theme.Colors.danger
        default:      return Theme.Colors.textPrimary
        }
    }
}
