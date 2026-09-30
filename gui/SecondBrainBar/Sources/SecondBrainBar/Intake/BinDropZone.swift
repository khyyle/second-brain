import SwiftUI

/// The drop zone, styled as a card that highlights on hover or drag.
///
/// The card takes documents (PDFs, notes, folders of them). Conversation
/// exports are a separate, rare action behind an explicit "Import" affordance
/// beneath the card, because their formats are provider-specific and a
/// misrouted multi-gigabyte export is an expensive mistake. If an export is
/// dropped on the card anyway, it isn't silently mishandled: the import
/// affordance turns into a one-click prompt for it.
///
/// The whole window accepts drops (see `DropContainerView`); the card is where
/// their outcome shows: whether dragged files will be accepted, a check and
/// count once they are added, and a brief error when nothing could be added.
struct BinDropZone: View {
    let bin: Bin
    @EnvironmentObject private var intake: DropIntake
    @State private var isHovering = false
    @State private var importHovering = false
    @State private var shakeProgress: CGFloat = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 7) {
            DropCardFace(
                state: state,
                idleTitle: bin.displayName,
                hint: bin.hint,
                isHovering: isHovering
            )
            .frame(height: Theme.Metric.zoneHeight)
            .contentShape(Rectangle())
            .onTapGesture { intake.presentAddFilesPanel() }
            .onHover { isHovering = $0 }
            .help("Drop files or a folder here, or click to browse (⌘O)")
            .modifier(Shake(progress: shakeProgress))
            .onChange(of: intake.failure) { failure in
                guard failure != nil, !reduceMotion else { return }
                withAnimation(.easeOut(duration: 0.3)) { shakeProgress += 1 }
            }
            importRow
        }
    }

    /// A drag in progress wins over a finished drop's result, so starting a
    /// new drag always shows whether it will be accepted. A copy still in
    /// flight keeps the accepting look until it has taken long enough to
    /// warrant a spinner.
    private var state: DropCardState {
        switch intake.drag {
        case .accepting: return .accepting
        case .rejecting: return .rejecting
        case .none: break
        }
        if intake.isAdding { return intake.addingIsSlow ? .adding : .accepting }
        if let added = intake.added { return .added(added) }
        if let failure = intake.failure { return .failed(failure) }
        return .idle
    }

    /// Deliberate "Import ChatGPT export…", which becomes a one-click prompt
    /// when an export was just dropped on the card by mistake.
    @ViewBuilder
    private var importRow: some View {
        if let pending = intake.pendingExport {
            HStack(spacing: 6) {
                Image(systemName: "questionmark.circle")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.Colors.accentAmber)
                Text("Looks like a \(pending.provider.displayName) export")
                    .font(Theme.Font.meta(10.5))
                    .foregroundStyle(Theme.Colors.textSecondary)
                    .lineLimit(1).truncationMode(.tail)
                Spacer(minLength: 6)
                Button { intake.importPending() } label: {
                    Text("Import")
                        .font(Theme.Font.meta(10).weight(.medium))
                        .foregroundStyle(Theme.Colors.accent)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Capsule().fill(Theme.Colors.accent.opacity(0.16)))
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 2)
        } else {
            HStack(spacing: 0) {
                Spacer(minLength: 0)
                Button { intake.presentImportPanel() } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "square.and.arrow.down")
                            .font(.system(size: 9, weight: .semibold))
                        Text("Import ChatGPT export")
                            .font(Theme.Font.meta(10))
                    }
                    .foregroundStyle(importHovering ? Theme.Colors.textSecondary : Theme.Colors.textTertiary)
                    .padding(.horizontal, 7).padding(.vertical, 3)
                    .background(
                        RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .fill(importHovering ? Theme.Colors.surfaceHover : Color.clear)
                    )
                }
                .buttonStyle(.plain)
                .onHover { importHovering = $0 }
                Spacer(minLength: 0)
            }
        }
    }
}

/// A brief horizontal shake, the macOS cue for rejected input. Each whole step
/// of `progress` plays one shake and comes to rest at zero offset.
private struct Shake: GeometryEffect {
    var progress: CGFloat
    private let travel: CGFloat = 4
    private let oscillations: CGFloat = 3

    init(progress: CGFloat) {
        self.progress = progress
    }

    var animatableData: CGFloat {
        get { progress }
        set { progress = newValue }
    }

    func effectValue(size: CGSize) -> ProjectionTransform {
        let offset = travel * sin(progress * .pi * 2 * oscillations)
        return ProjectionTransform(CGAffineTransform(translationX: offset, y: 0))
    }
}
