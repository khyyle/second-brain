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
