import SwiftUI

/// "Staged for build" heading. The first row names the model the build uses. The
/// second shows the estimated cost beside the Group and Build actions, or Stop while
/// compiling. The rows below leave an unknown cost blank; this line says so once.
struct StagedHeader: View {
    let cost: Double?
    let sourceCount: Int
    let stale: Bool
    let hasPlan: Bool
    let canPreview: Bool
    let onPreview: () -> Void
    let onBuild: () -> Void
    let canBuild: Bool
    let onOpenSettings: () -> Void
    @EnvironmentObject private var store: PipelineStore

    private var running: Bool {
        store.isGrouping || store.isCompiling
    }

    private var helpText: String {
        var text = "Sources that go into the wiki on the next build."
        if canPreview {
            text += " 'Group' bundles related conversations to avoid creating duplicate Second Brain entries."
        }
        text += " The cost is a rough upper bound."
        return text
    }

    var body: some View {
        let building = store.isCompiling || store.stopping
        let ready = !running && sourceCount > 0
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 5) {
                Text("Staged for build")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.Colors.textTertiary)
                HelpButton(text: helpText)
                Spacer(minLength: 8)
                if store.connected { BuildModelMenu(onOpenSettings: onOpenSettings) }
            }
            if building || ready {
                HStack(spacing: 8) {
                    if ready { costLine }
                    Spacer(minLength: 8)
                    if building { stopButton } else { actions }
                }
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
        Text(cost.map { formatEstimate($0) + (store.compilationSubscription ? atAPIPrices : "") } ?? "cost unknown")
            .font(Theme.Font.meta(10))
            .foregroundStyle(Theme.Colors.textTertiary)
            .monospacedDigit()
    }

    private var actions: some View {
        HStack(spacing: 8) {
            if canPreview {
                regroupButton
            }
            if canBuild && store.hasBuildModel {
                Button("Build wiki", action: onBuild)
                    .buttonStyle(PillButton(.primary))
            }
        }
    }

    // Grouping the same staged chats again gives the same result, so the button
    // only offers to run when it can change something: a first grouping, or a
    // regroup once new chats are staged. A stale plan is marked amber because
    // its sources outside the grouping would build on their own.
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
        } else if hasPlan {
            Text("Grouped")
                .font(Theme.Font.meta(10.5))
                .foregroundStyle(Theme.Colors.textTertiary)
        } else {
            Button("Group", action: onPreview)
                .buttonStyle(PillButton(.neutral))
        }
    }
}
