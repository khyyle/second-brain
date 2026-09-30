import SwiftUI

/// One health check as a table row: its label and flagged count. A passing
/// check reads as a quiet check mark; a check with issues expands inline.
struct HealthCategoryRow: View {
    let category: HealthCategory
    let onOpen: (String) -> Void
    var onDismiss: ((HealthItem) -> Void)? = nil
    var onMerge: ((String, String) -> Void)? = nil
    @State private var expanded = false
    @State private var hovering = false

    private var hasIssues: Bool { category.count > 0 }

    var body: some View {
        VStack(spacing: 1) {
            row
            if expanded {
                PaginatedList(items: category.items) { item in
                    HealthItemRow(item: item, onOpen: onOpen, onDismiss: onDismiss, onMerge: onMerge)
                }
            }
        }
    }

    private var row: some View {
        HStack(spacing: 9) {
            Image(systemName: hasIssues ? "chevron.right" : "checkmark")
                .font(.system(size: 9, weight: .semibold))
                .foregroundStyle(hasIssues ? Theme.Colors.textTertiary : Theme.Colors.success)
                .rotationEffect(.degrees(expanded ? 90 : 0))
                .frame(width: 12)
            Text(category.label)
                .font(Theme.Font.body(11.5))
                .foregroundStyle(hasIssues ? Theme.Colors.textPrimary : Theme.Colors.textSecondary)
            Spacer(minLength: 6)
            Text("\(category.count)")
                .font(Theme.Font.meta(9.5))
                .foregroundStyle(hasIssues ? Theme.Colors.textSecondary : Theme.Colors.textTertiary)
        }
        .modifier(RowBackground(hovering: hovering && hasIssues))
        .contentShape(Rectangle())
        .onTapGesture {
            guard hasIssues else { return }
            withAnimation(.easeInOut(duration: 0.15)) { expanded.toggle() }
        }
        .onHover { hovering = $0 }
        .animation(.easeInOut(duration: 0.12), value: hovering)
    }
}
