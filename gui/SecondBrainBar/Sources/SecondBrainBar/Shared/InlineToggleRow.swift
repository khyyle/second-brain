import SwiftUI

/// A left-aligned inline expand/collapse toggle, e.g. "+ 12 more clusters"
/// or "+ 224 ungrouped", collapsing back to "Show fewer". Used for both the
/// extra clusters and the single-source units, which are computed rather than
/// files, so they expand inline rather than opening Finder.
struct InlineToggleRow: View {
    let collapsedLabel: String
    let isExpanded: Bool
    let onToggle: () -> Void

    var body: some View {
        InlineActionRow(label: isExpanded ? "Show fewer" : collapsedLabel, action: onToggle)
    }
}
