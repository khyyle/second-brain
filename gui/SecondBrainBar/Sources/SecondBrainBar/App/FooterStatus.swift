import SwiftUI

/// Footer left slot: live pipeline status when a run is active, otherwise
/// the vault metrics.
struct FooterStatus: View {
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        Group {
            if store.isActive {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach([store.ingestStatus, store.buildStatus].compactMap { $0 }) { status in
                            HStack(spacing: 6) {
                                if status.queued {
                                    Image(systemName: "clock").font(.system(size: 10))
                                } else {
                                    ProgressView().controlSize(.small).scaleEffect(0.7)
                                }
                                Text(status.footerDetail(at: context.date))
                                    .font(Theme.Font.meta(10.5))
                                    .monospacedDigit()
                            }
                            .foregroundStyle(Theme.Colors.textSecondary)
                        }
                    }
                }
            } else {
                Text("\(store.stagedForBuild.count) staged · \(store.builtCount) built")
                    .font(Theme.Font.meta(10.5))
                    .foregroundStyle(Theme.Colors.textTertiary)
                    .monospacedDigit()
                    .fixedSize()
                    .lineLimit(1)
            }
        }
    }

}
