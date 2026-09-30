import SwiftUI

/// One flagged item under an expanded check
struct HealthItemRow: View {
    let item: HealthItem
    let onOpen: (String) -> Void
    var onDismiss: ((HealthItem) -> Void)? = nil
    var onMerge: ((String, String) -> Void)? = nil
    @State private var hovering = false

    private var openable: Bool { item.page != nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 9) {
                Spacer().frame(width: 12)
                title(item.text, page: item.page)
                Spacer(minLength: 6)
                if let onDismiss {
                    TrailingReserve(hovering: hovering) {
                        detailText
                    } hover: {
                        HStack(spacing: 4) {
                            mergeIcon
                            HoverIcon(systemName: "xmark.circle.fill",
                                      help: "Dismiss") {
                                onDismiss(item)
                            }
                        }
                    }
                } else {
                    detailText
                }
            }
            .contentShape(Rectangle())
            .onTapGesture { if let page = item.page { onOpen(page) } }
            if let pair = item.pair {
                HStack(spacing: 9) {
                    Spacer().frame(width: 22)
                    title("+ \(pair)", page: pair)
                    Spacer(minLength: 6)
                }
                .contentShape(Rectangle())
                .onTapGesture { onOpen(pair) }
            }
        }
        .modifier(RowBackground(hovering: hovering && openable))
        .onHover { hovering = $0 }
        .animation(.easeInOut(duration: 0.12), value: hovering)
    }

    // Direction is chosen in the confirmation alert ("Keep A" / "Keep B"),
    // so the icon itself is a plain hover button like dismiss.
    @ViewBuilder
    private var mergeIcon: some View {
        if let onMerge, let page = item.page, let pair = item.pair {
            HoverIcon(systemName: "arrow.merge",
                      help: "Merge") {
                onMerge(page, pair)
            }
        }
    }

    @ViewBuilder
    private var detailText: some View {
        if let detail = item.detail {
            Text(detail)
                .font(Theme.Font.meta(9.5))
                .foregroundStyle(Theme.Colors.textTertiary)
        }
    }

    // Page-backed lines open on click, so they carry the openable underline;
    // a line with no page (a broken link's missing target) stays inert text.
    @ViewBuilder
    private func title(_ text: String, page: String?) -> some View {
        let base = Text(text)
            .font(Theme.Font.body(11))
            .foregroundStyle(page != nil ? Theme.Colors.textSecondary : Theme.Colors.textTertiary)
            .lineLimit(1)
            .truncationMode(.middle)
        if page != nil {
            base.openableTitle(text)
        } else {
            base.help(text)
        }
    }
}
