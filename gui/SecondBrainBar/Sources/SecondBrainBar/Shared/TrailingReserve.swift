import SwiftUI

/// Holds a row's resting label and its hover action in one trailing slot,
/// reserving the wider of the two so swapping them on hover never changes the
/// title's available width and re-truncates a middle-clipped name.
struct TrailingReserve<Rest: View, Hover: View>: View {
    let hovering: Bool
    @ViewBuilder var rest: () -> Rest
    @ViewBuilder var hover: () -> Hover

    var body: some View {
        ZStack(alignment: .trailing) {
            rest().opacity(hovering ? 0 : 1)
            hover().opacity(hovering ? 1 : 0).allowsHitTesting(hovering)
        }
    }
}
