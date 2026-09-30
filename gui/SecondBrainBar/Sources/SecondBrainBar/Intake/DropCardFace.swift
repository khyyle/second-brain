import SwiftUI

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
