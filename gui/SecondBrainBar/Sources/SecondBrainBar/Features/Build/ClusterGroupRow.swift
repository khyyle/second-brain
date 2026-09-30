import SwiftUI

/// A build plan unit: one expandable group of related chats (with split and
/// per-source pop-out tuning), or a plain row for a single-source unit.
struct ClusterGroupRow: View {
    let group: ClusterGroup
    let model: String
    @Binding var overrides: ClusterOverrides
    let onCommit: () -> Void
    let onRemove: (String) -> Void
    let onOpen: (String) -> Void
    @State private var expanded = false
    @State private var hovering = false
    @EnvironmentObject private var store: PipelineStore

    private var isMulti: Bool { group.members.count > 1 }
    private var isSplit: Bool { overrides.isSplit(group.id) }

    var body: some View {
        VStack(spacing: 1) {
            header
            if expanded {
                ForEach(group.members, id: \.rel) { member in
                    ClusterMemberRow(
                        member: member,
                        excluded: overrides.isExcluded(member.rel),
                        onToggleExclude: {
                            overrides.toggleExcluded(member.rel)
                            onCommit()
                        },
                        onRemove: { onRemove(member.rel) },
                        onOpen: { onOpen(member.rel) }
                    )
                }
            }
        }
    }

    private var header: some View {
        HStack(spacing: 9) {
            Image(systemName: isMulti ? (expanded ? "chevron.down" : "chevron.right") : "circle.dashed")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Theme.Colors.textSecondary)
                .frame(width: 12)
            title
            if isMulti {
                Text("\(group.members.count) chats" + (isSplit ? " · split" : ""))
                    .font(Theme.Font.meta(9.5))
                    .foregroundStyle(isSplit ? Theme.Colors.accentAmber : Theme.Colors.textTertiary)
            }
            Spacer(minLength: 6)
            trailing
        }
        .modifier(RowBackground(hovering: hovering))
        .contentShape(Rectangle())
        .onTapGesture {
            if isMulti { expanded.toggle() } else { onOpen(group.members.first?.rel ?? "") }
        }
        .onHover { hovering = $0 }
    }

    // A single-source unit opens its file on click, so its title carries the
    // openable underline affordance; a multi-chat group's title toggles the
    // group open instead, so it only gets a tooltip.
    @ViewBuilder
    private var title: some View {
        let name = cleanName(group.title)
        let base = Text(name)
            .font(Theme.Font.body(11.5))
            .foregroundStyle(Theme.Colors.textPrimary)
            .lineLimit(1)
            .truncationMode(.middle)
        if isMulti {
            base.help(name)
        } else {
            base.openableTitle(name)
        }
    }

    // A multi-chat group swaps its cost for a Split/Merge pill on hover. The
    // pill is taller than the text, so both live in a ZStack that always
    // reserves the pill's height, keeping the row from reflowing on hover. A
    // single-source row instead reveals its remove button on hover.
    @ViewBuilder
    private var trailing: some View {
        if isMulti {
            TrailingReserve(hovering: hovering) {
                cost
            } hover: {
                Button(isSplit ? "Merge" : "Split") {
                    overrides.toggleSplit(group.id)
                    onCommit()
                }
                .buttonStyle(PillButton(isSplit ? .confirm : .quiet))
                .disabled(store.locked)
            }
        } else {
            TrailingReserve(hovering: hovering) {
                cost
            } hover: {
                HoverIcon(systemName: "xmark.circle.fill",
                          help: "Remove from staging (moves the file to Trash)") {
                    onRemove(group.members.first?.rel ?? "")
                }
                .disabled(store.locked)
            }
        }
    }

    private var cost: some View {
        Text(String(format: "~$%.2f", group.cost(for: model)))
            .font(Theme.Font.meta(9.5))
            .foregroundStyle(Theme.Colors.textTertiary)
            .monospacedDigit()
    }
}
