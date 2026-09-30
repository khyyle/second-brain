import SwiftUI

/// Small section heading used inside each tab (e.g. "In progress", "Recent").
/// An optional `help` string adds a "?" that reveals an explanation in a
/// popover when clicked.
struct SectionHeader: View {
    let title: String
    var help: String? = nil

    var body: some View {
        HStack(spacing: 5) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.Colors.textTertiary)
            if let help { HelpButton(text: help) }
            Spacer()
        }
        .padding(.horizontal, 8)
        .padding(.top, 4)
        .padding(.bottom, 1)
    }
}
