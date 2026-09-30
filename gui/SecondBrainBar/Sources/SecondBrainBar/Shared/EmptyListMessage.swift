import SwiftUI

/// Shared empty / loading state for a section.
struct EmptyListMessage: View {
    let text: String?
    var body: some View {
        Group {
            if let text {
                Text(text)
                    .font(Theme.Font.body(11.5))
                    .foregroundStyle(Theme.Colors.textSecondary)
            } else {
                ProgressView().controlSize(.small)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
    }
}
