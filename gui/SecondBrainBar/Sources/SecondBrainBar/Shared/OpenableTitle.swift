import SwiftUI

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
