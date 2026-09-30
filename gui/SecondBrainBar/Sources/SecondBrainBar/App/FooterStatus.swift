import SwiftUI

/// Footer left slot: live pipeline status when a run is active, otherwise
/// the vault metrics.
struct FooterStatus: View {
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        Group {
            if let status = store.status, status.isActive {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small).scaleEffect(0.7)
                        Text(activeDetail(status, now: context.date))
                            .font(Theme.Font.meta(10.5))
                            .foregroundStyle(Theme.Colors.textSecondary)
                            .monospacedDigit()
                    }
                }
            } else {
                Text("\(store.staged.count) staged · \(store.builtCount) built")
                    .font(Theme.Font.meta(10.5))
                    .foregroundStyle(Theme.Colors.textTertiary)
                    .monospacedDigit()
                    .fixedSize()
                    .lineLimit(1)
            }
        }
    }

    // The single live-progress readout: phase + i/n for every stage, with
    // elapsed and cost added for the paid build (the free local stages have
    // no cost to show).
    private func activeDetail(_ status: PipelineStatus, now: Date) -> String {
        let verb: String
        switch status.phase {
        case "compile": verb = "Building"
        case "cluster": verb = "Grouping"
        case "triage":  verb = "Triaging"
        default:        verb = "Ingesting"
        }
        var parts: [String] = [
            status.total > 0
                ? "\(verb) \(min(status.current + 1, status.total))/\(status.total)"
                : verb
        ]
        if status.phase == "compile" {
            parts.append(formatElapsed(max(0, now.timeIntervalSince(status.startedAt))))
            if status.costUSD > 0 { parts.append(String(format: "$%.2f", status.costUSD)) }
        }
        return parts.joined(separator: " · ")
    }
}
