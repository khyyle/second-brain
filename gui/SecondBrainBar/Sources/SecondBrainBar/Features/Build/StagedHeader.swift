import SwiftUI

/// "Staged for build" heading. Shows staged count + cost (the plan's when a
/// cluster preview is active, else a per-source estimate), a Preview/Refresh
/// action, and live progress while grouping or compiling.
struct StagedHeader: View {
    let cost: Double
    let sourceCount: Int
    let stale: Bool
    let hasPlan: Bool
    let canPreview: Bool
    let onPreview: () -> Void
    let onBuild: () -> Void
    let canBuild: Bool
    @EnvironmentObject private var store: PipelineStore

    private var running: Bool {
        store.isGrouping || store.isCompiling
    }

    private var helpText: String {
        var text = "Sources that go into the wiki on the next build."
        if canPreview {
            text += " 'Group' bundles related conversations to avoid creating duplicate Second Brain entries."
        }
        if store.skippedCount > 0 {
            text += " Skipped chats can be restored until the next build."
        }
        text += " The cost is a rough upper bound."
        return text
    }

    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 5) {
                    Text("Staged for build")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.Colors.textTertiary)
                    HelpButton(text: helpText)
                }
                if !running, sourceCount > 0 {
                    costLine
                }
            }
            Spacer(minLength: 8)
            if store.isCompiling || store.stopping {
                stopButton
            } else if !running, sourceCount > 0 {
                actions
            }
        }
        .padding(.horizontal, 8).padding(.top, 4).padding(.bottom, 1)
    }

    /// While a compile runs, the primary action becomes Stop in the same slot
    /// the Build button occupied — then a disabled Stopping until it winds down.
    private var stopButton: some View {
        Button(store.stopping ? "Stopping" : "Stop", action: store.requestStop)
            .buttonStyle(PillButton(.destructive))
            .disabled(store.stopping)
    }

    private var costLine: some View {
        Text("~$\(String(format: "%.2f", cost))")
            .font(Theme.Font.meta(10))
            .foregroundStyle(Theme.Colors.textTertiary)
            .monospacedDigit()
    }

    private var actions: some View {
        HStack(spacing: 8) {
            if canPreview {
                regroupButton
            }
            if canBuild {
                Button("Build wiki", action: onBuild)
                    .buttonStyle(PillButton(.primary))
            }
        }
    }

    // A stale plan no longer matches the staged set, so its grouping — and the
    // cost saving that comes with it — is dropped on the next build unless it is
    // recomputed first. Mark that case amber with a refresh glyph; an absent or
    // current plan stays neutral.
    @ViewBuilder
    private var regroupButton: some View {
        if stale {
            Button(action: onPreview) {
                HStack(spacing: 3) {
                    Image(systemName: "arrow.triangle.2.circlepath")
                    Text("Regroup")
                }
            }
            .buttonStyle(PillButton(tint: Theme.Colors.accentAmber))
        } else {
            Button(hasPlan ? "Regroup" : "Group", action: onPreview)
                .buttonStyle(PillButton(.neutral))
        }
    }
}
