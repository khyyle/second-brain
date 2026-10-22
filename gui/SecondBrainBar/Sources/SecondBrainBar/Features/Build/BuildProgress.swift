import SwiftUI

/// What the build agent is doing for the row being built, shown under its title.
struct BuildActivityLine: View {
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        if let activity = store.buildProgress?.currentActivity, !activity.isEmpty {
            Text(activity)
                .font(Theme.Font.meta(9.5))
                .foregroundStyle(Theme.Colors.textTertiary)
                .lineLimit(1)
                .truncationMode(.tail)
        }
    }
}

/// Elapsed time and cost so far for the row being built, updated each second.
struct BuildElapsed: View {
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            Text(detail(now: context.date))
                .font(Theme.Font.meta(9.5))
                .foregroundStyle(Theme.Colors.textTertiary)
                .monospacedDigit()
        }
    }

    private func detail(now: Date) -> String {
        guard let progress = store.buildProgress else { return "" }
        var parts: [String] = []
        if let started = progress.currentStartedAt {
            parts.append(formatElapsed(max(0, now.timeIntervalSince(started))))
        }
        if progress.currentCostUSD > 0 {
            parts.append(String(format: "$%.2f", progress.currentCostUSD))
        }
        return parts.joined(separator: " · ")
    }
}
