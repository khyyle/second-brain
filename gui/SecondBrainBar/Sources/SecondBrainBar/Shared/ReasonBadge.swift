import SwiftUI

/// A row's status label that opens why on click
struct ReasonBadge: View {
    let label: String
    let tint: Color
    let note: String
    let accessibilityHint: String
    @State private var hovering = false
    @State private var showingNote = false

    var body: some View {
        Button { showingNote.toggle() } label: {
            Text(label)
                .font(Theme.Font.meta(9.5).weight(.medium))
                .foregroundStyle(tint)
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(Capsule().fill(tint.opacity(hovering ? 0.24 : 0.14)))
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .accessibilityHint(accessibilityHint)
        .popover(isPresented: $showingNote, arrowEdge: .bottom) {
            PopoverNote(text: note)
        }
    }
}
