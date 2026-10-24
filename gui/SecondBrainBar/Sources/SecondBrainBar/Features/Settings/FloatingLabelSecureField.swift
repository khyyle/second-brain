import SwiftUI

struct FloatingLabelSecureField: View {
    let title: String
    @Binding var text: String
    @FocusState private var isFocused: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var isLabelRaised: Bool { isFocused || !text.isEmpty }

    var body: some View {
        ZStack(alignment: .leading) {
            SecureField(title, text: $text, prompt: Text(""))
                .textFieldStyle(.plain)
                .font(Theme.Font.body(12))
                .foregroundStyle(Theme.Colors.textPrimary)
                .focused($isFocused)
                .padding(.top, 14)
                .accessibilityLabel(title)

            Text(title)
                .font(Theme.Font.body(12))
                .foregroundStyle(isFocused ? Theme.Colors.accent : Theme.Colors.textSecondary)
                .scaleEffect(isLabelRaised ? 0.8 : 1, anchor: .leading)
                .offset(y: isLabelRaised ? -12 : 0)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
        .padding(.horizontal, 12)
        .frame(height: 52)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(Theme.Colors.background)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(isFocused ? Theme.Colors.accent : Theme.Colors.stroke, lineWidth: 1)
        )
        .contentShape(Rectangle())
        .onTapGesture { isFocused = true }
        .animation(
            reduceMotion ? nil : .timingCurve(0.4, 0, 0.2, 1, duration: 0.3),
            value: isLabelRaised
        )
    }
}
