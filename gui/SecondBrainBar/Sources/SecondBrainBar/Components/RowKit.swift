import SwiftUI

/// Standard hoverable row background.
struct RowBackground: ViewModifier {
    let hovering: Bool
    func body(content: Content) -> some View {
        content
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .background(
                RoundedRectangle(cornerRadius: Theme.Metric.cornerSmall, style: .continuous)
                    .fill(hovering ? Theme.Colors.surfaceHover : Color.clear)
            )
    }
}

/// Marks a row's clickable title: a full-text tooltip plus a bottom rule while
/// the pointer is over the text. The rule is an overlay, so it never shifts the
/// text the way an underline would.
struct OpenableTitle: ViewModifier {
    let full: String
    @State private var hovering = false

    func body(content: Content) -> some View {
        content
            .overlay(alignment: .bottom) {
                if hovering {
                    Rectangle()
                        .frame(height: 1)
                        .foregroundStyle(Theme.Colors.textSecondary)
                }
            }
            .help(full)
            .onHover { hovering = $0 }
    }
}

extension View {
    func openableTitle(_ full: String) -> some View {
        modifier(OpenableTitle(full: full))
    }
}

/// Holds a row's resting label and its hover action in one trailing slot,
/// reserving the wider of the two so swapping them on hover never changes the
/// title's available width and re-truncates a middle-clipped name.
struct TrailingReserve<Rest: View, Hover: View>: View {
    let hovering: Bool
    @ViewBuilder var rest: () -> Rest
    @ViewBuilder var hover: () -> Hover

    var body: some View {
        ZStack(alignment: .trailing) {
            rest().opacity(hovering ? 0 : 1)
            hover().opacity(hovering ? 1 : 0).allowsHitTesting(hovering)
        }
    }
}
