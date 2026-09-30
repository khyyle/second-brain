import SwiftUI

/// Titled card. The optional "?" beside the title is for groups whose title
/// names a single non-obvious concept; per-
/// field help lives on the `SettingsRow` instead.
struct SettingsGroup<Content: View>: View {
    let title: String
    var help: String? = nil
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            SectionHeader(title: title, help: help)
            VStack(alignment: .leading, spacing: 9) {
                content
            }
            .padding(11)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: Theme.Metric.cornerSmall, style: .continuous)
                    .fill(Theme.Colors.surface.opacity(0.5))
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.Metric.cornerSmall, style: .continuous)
                    .strokeBorder(Theme.Colors.stroke, lineWidth: 1)
            )
        }
    }
}
