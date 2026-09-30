import SwiftUI

/// The drop zone, styled as a card that highlights on hover or drag.
///
/// The card takes documents and conversation exports. An export is recognized
/// by its contents and imported after a confirmation that names it and says how
/// many conversations it holds, because sorting that many conversations ties up
/// the pipeline for a long time.
///
/// The whole window accepts drops (see `DropContainerView`); the card is where
/// their outcome shows: whether dragged files will be accepted, a check and
/// count once they are added, and a brief error when nothing could be added.
struct BinDropZone: View {
    let bin: Bin
    @EnvironmentObject private var intake: DropIntake
    @State private var isHovering = false
    @State private var shakeProgress: CGFloat = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
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
