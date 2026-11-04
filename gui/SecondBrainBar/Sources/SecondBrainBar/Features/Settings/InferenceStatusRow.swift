import SwiftUI

struct InferenceStatusRow: View {
    let title: String
    let status: InferenceWorkerStatus?
    let checking: Bool
    let receivedStatus: Bool

    private var label: String {
        if checking { return "Checking…" }
        guard let status else { return receivedStatus ? "Not running" : "Unknown" }
        return status.phase == "error" ? "Failed" : status.phase.capitalized
    }

    private var color: Color {
        if checking || !receivedStatus { return Theme.Colors.textTertiary }
        guard let status else { return Theme.Colors.danger }
        switch status.phase {
        case "ready": return Theme.Colors.success
        case "error": return Theme.Colors.danger
        default: return Theme.Colors.accentAmber
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            SettingsRow(title) {
                Text(label)
                    .font(Theme.Font.meta(10))
                    .foregroundStyle(color)
            }
            if let status {
                Text(status.model)
                    .font(Theme.Font.meta(10))
                    .foregroundStyle(Theme.Colors.textSecondary)
                if ["downloading", "verifying"].contains(status.phase), status.total > 0 {
                    ProgressView(value: Double(min(status.completed, status.total)), total: Double(status.total))
                        .accessibilityLabel("\(title) model \(status.phase)")
                    Text("\(ByteCountFormatter.string(fromByteCount: status.completed, countStyle: .file)) of \(ByteCountFormatter.string(fromByteCount: status.total, countStyle: .file))")
                        .font(Theme.Font.meta(10))
                        .foregroundStyle(Theme.Colors.textSecondary)
                }
                if let error = status.error {
                    Text(error)
                        .font(Theme.Font.meta(10))
                        .foregroundStyle(Theme.Colors.danger)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}
