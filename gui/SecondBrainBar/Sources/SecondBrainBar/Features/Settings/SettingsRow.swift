import SwiftUI

/// A label (with optional field-level "?") on the left and a trailing control.
struct SettingsRow<Control: View>: View {
    let label: String
    var help: String? = nil
    @ViewBuilder let control: Control

    init(_ label: String, help: String? = nil, @ViewBuilder control: () -> Control) {
        self.label = label
        self.help = help
        self.control = control()
    }

    var body: some View {
        HStack(spacing: 5) {
            Text(label)
                .font(Theme.Font.body(11.5))
                .foregroundStyle(Theme.Colors.textPrimary)
            if let help { HelpButton(text: help) }
            Spacer(minLength: 8)
            control
        }
    }
}
