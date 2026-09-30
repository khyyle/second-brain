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
    @ViewBuilder let row: (Item) -> RowContent
    @State private var expandedTo = 0  // 0 means the initial window

    private var limit: Int { min(expandedTo == 0 ? initial : expandedTo, items.count) }

    var body: some View {
        LazyVStack(spacing: 1) {
            ForEach(items.prefix(limit)) { row($0) }
        }
        if items.count > limit {
            InlineActionRow(label: "+ \(items.count - limit) more") {
                expandedTo = min(limit + step, items.count)
            }
        }
        if limit > initial {
            InlineActionRow(label: "Show fewer") { expandedTo = 0 }
        }
    }
}
