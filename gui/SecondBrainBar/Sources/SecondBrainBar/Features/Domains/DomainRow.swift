import SwiftUI

/// One domain row: its name, page count, and a hover menu for rename / merge /
/// delete. Tapping the row opens its generated domain view.
struct DomainRow: View {
    let domain: DomainInfo
    let others: [String]
    let busy: Bool
    let onOpen: () -> Void
    let onRename: () -> Void
    let onMerge: (String) -> Void
    let onDelete: () -> Void
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    var body: some View {
        HStack(spacing: 9) {
            Image(systemName: "tag")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Theme.Colors.textTertiary)
                .frame(width: 12)
            Text(domain.name)
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
                .lineLimit(1).truncationMode(.middle)
                .openableTitle(domain.name)
            Spacer(minLength: 6)
            menu
                .opacity(hovering && !busy ? 1 : 0)
                .allowsHitTesting(hovering && !busy)
                .disabled(store.locked)
            count
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
        .animation(.easeInOut(duration: 0.12), value: hovering)
    }

    private var count: some View {
        Text("\(domain.pageCount)")
            .font(Theme.Font.meta(9.5))
            .foregroundStyle(Theme.Colors.textTertiary)
            .help(domain.pageCount == 1 ? "1 page" : "\(domain.pageCount) pages")
    }

    private var menu: some View {
        Menu {
            Button("Rename…", action: onRename)
            if !others.isEmpty {
                Menu("Merge into") {
                    ForEach(others, id: \.self) { other in
                        Button(other) { onMerge(other) }
                    }
                }
            }
            Divider()
            Button("Delete", role: .destructive, action: onDelete)
        } label: {
            Image(systemName: "ellipsis.circle")
                .font(.system(size: 11))
                .foregroundStyle(Theme.Colors.textSecondary)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .frame(width: 16)
    }
}
