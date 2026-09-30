import SwiftUI

/// Editable list of run times (whole hours, 24h). Add / remove rows and
/// pick each hour from a menu -- no free-text parsing.
struct ScheduleEditor: View {
    @Binding var hours: [Int]

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ForEach(hours, id: \.self) { h in
                ScheduleTimeRow(
                    hour: h,
                    onChange: { change(from: h, to: $0) },
                    onRemove: { hours.removeAll { $0 == h } }
                )
            }

            TextAction(title: "Add time", icon: "plus", restTint: Theme.Colors.accent, action: add)
        }
    }

    private func change(from old: Int, to new: Int) {
        var set = Set(hours)
        set.remove(old)
        set.insert(new)
        hours = set.sorted()
    }

    private func add() {
        // Default to the next upcoming local hour
        let currentHour = Calendar.current.component(.hour, from: Date())
        let upcoming = (1...24).map { (currentHour + $0) % 24 }
        let candidate = upcoming.first { !hours.contains($0) } ?? currentHour
        hours = Set(hours).union([candidate]).sorted()
    }
}

/// Format a whole-hour slot in the user's local clock 
private func formatHour(_ hour: Int, withZone: Bool) -> String {
    let date = Calendar.current.date(bySettingHour: hour, minute: 0, second: 0, of: Date()) ?? Date()
    var style = Date.FormatStyle.dateTime
        .hour(.defaultDigits(amPM: .abbreviated))
        .minute(.twoDigits)
    if withZone {
        style = style.timeZone(.specificName(.short))
    }
    return date.formatted(style)
}

/// One scheduled time: a pill showing the time in local AM/PM plus zone that
/// opens an hour picker, and a remove button. Both reuse the app's shared
/// button primitives, so they highlight on hover like every other control.
private struct ScheduleTimeRow: View {
    let hour: Int
    let onChange: (Int) -> Void
    let onRemove: () -> Void
    @State private var picking = false

    var body: some View {
        HStack(spacing: 8) {
            Button { picking = true } label: {
                HStack(spacing: 5) {
                    Image(systemName: "clock")
                    Text(formatHour(hour, withZone: true))
                }
            }
            .buttonStyle(PillButton(.neutral))
            .help("Change this time")
            .popover(isPresented: $picking, arrowEdge: .bottom) {
                HourPicker(selected: hour) { onChange($0); picking = false }
            }

            Spacer(minLength: 6)

            HoverIcon(systemName: "xmark.circle.fill", help: "Remove this time", action: onRemove)
        }
    }
}

/// Hour picker shown in the schedule pill's popover: two columns, AM then PM,
/// so all 24 slots stay visible at once without scrolling.
private struct HourPicker: View {
    let selected: Int
    let onPick: (Int) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            column(0..<12)
            column(12..<24)
        }
        .padding(10)
    }

    private func column(_ hours: Range<Int>) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(hours, id: \.self) { hour in
                TextAction(
                    title: formatHour(hour, withZone: false),
                    restTint: hour == selected ? Theme.Colors.accent : Theme.Colors.textSecondary
                ) { onPick(hour) }
                .frame(width: 72, alignment: .leading)
            }
        }
    }
}
