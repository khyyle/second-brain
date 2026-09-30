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

/// What a drop target is showing.
enum DropCardState: Equatable {
    case idle
    case accepting
    case rejecting
    case adding
    case added(AddedFiles)
    case failed(DropIntake.Failure)
}

/// A drop target's face: icon, title, and a detail line on a surface that
/// highlights. The Ingest card and the window-wide drop overlay share it, so a
/// drop target always looks the same.
///
/// Titles and icons swap instantly, because crossfading text blurs it and makes
/// the card feel slow. Only the surface eases, and the added state's check
/// draws itself in as the single animated confirmation.
struct DropCardFace: View {
    let state: DropCardState
    let idleTitle: String
    let hint: String
    var isHovering = false

    private let iconSlotHeight: CGFloat = 26

    var body: some View {
        ZStack {
            ZStack {
                RoundedRectangle(cornerRadius: Theme.Metric.corner, style: .continuous)
                    .fill(isHighlighted ? Theme.Colors.surfaceHover : Theme.Colors.surface)
                RoundedRectangle(cornerRadius: Theme.Metric.corner, style: .continuous)
                    .strokeBorder(strokeColor, lineWidth: 1)
            }
            .animation(.easeOut(duration: 0.15), value: state)
            .animation(.easeOut(duration: 0.14), value: isHovering)

            VStack(spacing: 9) {
                icon.frame(height: iconSlotHeight)
                VStack(spacing: 3) {
                    Text(title)
                        .font(Theme.Font.body(12.5, weight: .medium))
                        .foregroundStyle(titleColor)
                    if let detail {
                        Text(detail)
                            .font(Theme.Font.body(10.5))
                            .foregroundStyle(Theme.Colors.textSecondary)
                    }
                }
                .lineLimit(1)
                .truncationMode(.middle)
                .padding(.horizontal, 10)
            }
        }
    }

    @ViewBuilder
    private var icon: some View {
        switch state {
        case .adding:
            ProgressView().controlSize(.small)
        case .added(let files):
            DrawnCheckmark(color: Theme.Colors.success)
                .id(files.id)
        case .idle, .accepting, .rejecting, .failed:
            Image(systemName: symbolName)
                .font(.system(size: 22, weight: .regular))
                .foregroundStyle(iconColor)
        }
    }

    private var isHighlighted: Bool {
        switch state {
        case .accepting, .adding: return true
        case .idle: return isHovering
        case .rejecting, .added, .failed: return false
        }
    }

    /// Outcome tint for the icon and border. Each outcome also has its own
    /// icon and title, so color never carries the state alone.
    private var outcomeColor: Color? {
        switch state {
        case .rejecting, .failed: return Theme.Colors.danger
        case .added: return Theme.Colors.success
        case .idle, .accepting, .adding: return nil
        }
    }

    private var symbolName: String {
        switch state {
        case .rejecting: return "nosign"
        case .failed: return "exclamationmark.triangle"
        case .idle, .accepting, .adding, .added: return Bin.inbox.iconName
        }
    }

    private var title: String {
        switch state {
        case .idle, .accepting: return idleTitle
        case .rejecting: return "Can't add these files"
        case .adding: return "Adding files…"
        case .added(let files): return files.count == 1 ? "Added 1 file" : "Added \(files.count) files"
        case .failed(let failure): return failure.title
        }
    }

    private var detail: String? {
        switch state {
        case .added: return nil
        case .failed(let failure): return failure.detail ?? hint
        case .idle, .accepting, .rejecting, .adding: return hint
        }
    }

    private var iconColor: Color {
        outcomeColor ?? (isHighlighted ? Theme.Colors.textPrimary : Theme.Colors.textSecondary)
    }

    private var titleColor: Color {
        switch state {
        case .rejecting, .failed: return Theme.Colors.danger
        case .idle, .accepting, .adding, .added: return Theme.Colors.textPrimary
        }
    }

    private var strokeColor: Color {
        if let outcomeColor { return outcomeColor.opacity(0.6) }
        return isHighlighted ? Theme.Colors.textTertiary : Theme.Colors.stroke
    }
}

/// A check that draws itself in, confirming files were added. With Reduce
/// Motion on it appears already drawn.
private struct DrawnCheckmark: View {
    let color: Color
    @State private var progress: CGFloat = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let diameter: CGFloat = 24

    var body: some View {
        ZStack {
            Circle()
                .strokeBorder(color.opacity(0.5), lineWidth: 1.5)
            CheckmarkShape()
                .trim(from: 0, to: progress)
                .stroke(color, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                .padding(diameter * 0.28)
        }
        .frame(width: diameter, height: diameter)
        .onAppear {
            if reduceMotion {
                progress = 1
            } else {
                withAnimation(.easeOut(duration: 0.25)) { progress = 1 }
            }
        }
    }
}

private struct CheckmarkShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.minY + rect.height * 0.55))
        path.addLine(to: CGPoint(x: rect.minX + rect.width * 0.38, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        return path
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
