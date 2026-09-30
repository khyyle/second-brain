import SwiftUI

/// A small "?" that reveals an explanation in a popover on click. Used beside
/// specific non-obvious fields.
struct HelpButton: View {
    let text: String
    var size: CGFloat = 10
    @State private var show = false
    @State private var hovering = false

    var body: some View {
        Button { show.toggle() } label: {
            Image(systemName: "questionmark.circle")
                .font(.system(size: size))
                .foregroundStyle(hovering ? Theme.Colors.textPrimary : Theme.Colors.textTertiary)
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .popover(isPresented: $show, arrowEdge: .bottom) {
            PopoverNote(text: text)
        }
    }
}
