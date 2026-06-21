import SwiftUI
import AppKit

/// A single drop result, surfaced to the confirmation overlay.
struct UploadEvent: Equatable, Identifiable {
    let id = UUID()
    let count: Int
}

/// Shared state driving the post-drop confirmation: a soft sound, a
/// blur-and-check overlay, and an automatic dismiss after a beat.
///
/// Owned by `ContentView` as a `StateObject` and handed to the drop
/// zone so a successful copy can raise the overlay.
@MainActor
final class UploadFeedback: ObservableObject {
    @Published var event: UploadEvent?

    private var dismissWork: DispatchWorkItem?
    private let visibleDuration: TimeInterval = 1.5

    /// Raise the confirmation overlay for freshly added files.
    func confirm(count: Int) {
        dismissWork?.cancel()

        withAnimation(.spring(response: 0.38, dampingFraction: 0.72)) {
            event = UploadEvent(count: count)
        }
        playPing()

        let work = DispatchWorkItem { [weak self] in
            withAnimation(.easeOut(duration: 0.3)) { self?.event = nil }
        }
        dismissWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + visibleDuration, execute: work)
    }

    /// Soft, brief confirmation sound (a gentle "pop", not the flat
    /// no-action Tink). Falls back silently if unavailable.
    private func playPing() {
        if let sound = NSSound(named: NSSound.Name("Pop")) {
            sound.volume = 0.35
            sound.play()
        }
    }
}
