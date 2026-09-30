import SwiftUI

/// Compact two-or-more option segmented control in the popover theme.
struct SegmentedPicker<Value: Equatable>: View {
    let options: [(String, Value)]
    @Binding var selection: Value

    var body: some View {
        HStack(spacing: 2) {
            ForEach(options.indices, id: \.self) { i in
                let opt = options[i]
                let selected = opt.1 == selection
                Text(opt.0)
                    .font(Theme.Font.body(11.5, weight: selected ? .semibold : .regular))
                    .foregroundStyle(selected ? Theme.Colors.textPrimary : Theme.Colors.textSecondary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 5)
                    .background(
                        RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .fill(selected ? Theme.Colors.surfaceHover : .clear)
                    )
                    .contentShape(Rectangle())
                    .onTapGesture { selection = opt.1 }
            }
        }
        .padding(3)
        .background(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(Theme.Colors.background)
        )
    }
}
