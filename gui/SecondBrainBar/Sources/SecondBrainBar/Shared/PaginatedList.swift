import SwiftUI

/// Global cap on how many rows any list renders. A compact panel can't
/// usefully show a long scroll (Miller's law backs this); the true total is shown
/// separately, and Reveal opens the full set in Finder.
enum ListCap { static let max = 15 }

/// A capped list that expands inline in pages — with a collapse — instead of
/// overflowing into Finder. A small overflow fills in with one tap; a large
/// queue pages through, so the popover stays bounded either way.
struct PaginatedList<Item: Identifiable, RowContent: View>: View {
    let items: [Item]
    var initial: Int = ListCap.max
    var step: Int = 200
    /// When set, how far the list is expanded survives switching tabs.
    var expansionKey: String? = nil
    @ViewBuilder let row: (Item) -> RowContent
    @State private var localExpandedTo = 0  // 0 means the initial window
    @EnvironmentObject private var expansion: ListExpansion

    private var expandedTo: Int {
        expansionKey.map { expansion.pageLimit(for: $0) } ?? localExpandedTo
    }

    private func setExpandedTo(_ value: Int) {
        if let expansionKey {
            expansion.setPageLimit(value, for: expansionKey)
        } else {
            localExpandedTo = value
        }
    }

    private var limit: Int { min(expandedTo == 0 ? initial : expandedTo, items.count) }

    var body: some View {
        LazyVStack(spacing: 1) {
            ForEach(items.prefix(limit)) { row($0) }
        }
        if items.count > limit {
            InlineActionRow(label: "+ \(items.count - limit) more") {
                setExpandedTo(min(limit + step, items.count))
            }
        }
        if limit > initial {
            InlineActionRow(label: "Show fewer") { setExpandedTo(0) }
        }
    }
}
