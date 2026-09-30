import SwiftUI

/// The body of an explanation popover: a short paragraph at a readable width.
/// Popovers are separate windows that macOS places to fit on screen, so one
/// opened near an edge of the panel is never clipped by it.
struct PopoverNote: View {
    let text: String

    var body: some View {
        Text(text)
            .font(Theme.Font.body(12))
            .foregroundStyle(Theme.Colors.textPrimary)
            .frame(width: 260, alignment: .leading)
            .padding(12)
    }
}
