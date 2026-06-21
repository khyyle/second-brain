import SwiftUI

/// Full-popover confirmation shown over a blurred background after a
/// successful drop: a scrim, an animated check, and the file count.
struct UploadOverlay: View {
    let event: UploadEvent
    @State private var appeared = false

    var body: some View {
        ZStack {
            Theme.Colors.background.opacity(0.6)

            VStack(spacing: 13) {
                ZStack {
                    Circle()
                        .fill(Theme.Colors.success.opacity(0.16))
                        .frame(width: 62, height: 62)
                    Circle()
                        .strokeBorder(Theme.Colors.success.opacity(0.5), lineWidth: 1.5)
                        .frame(width: 62, height: 62)
                    Image(systemName: "checkmark")
                        .font(.system(size: 26, weight: .semibold))
                        .foregroundStyle(Theme.Colors.success)
                }
                .scaleEffect(appeared ? 1 : 0.4)
                .opacity(appeared ? 1 : 0)

                Text(title)
                    .font(Theme.Font.body(13, weight: .semibold))
                    .foregroundStyle(Theme.Colors.textPrimary)
                    .opacity(appeared ? 1 : 0)
                    .offset(y: appeared ? 0 : 6)
            }
        }
        .onAppear {
            withAnimation(.spring(response: 0.42, dampingFraction: 0.62)) {
                appeared = true
            }
        }
    }

    private var title: String {
        event.count == 1 ? "Added 1 file" : "Added \(event.count) files"
    }
}
