import SwiftUI

/// A completed run's notice, dismissed independently for each pipeline stage.
struct RunOutcomeBanner: View {
    let notice: RunNotice
    let onDismiss: () -> Void

    var body: some View {
        NoticeBanner(headline: notice.headline, reason: notice.run.reason,
                     symbolName: notice.symbolName, tint: notice.tint, onDismiss: onDismiss)
    }
}

extension RunNotice {
    var headline: String {
        let stageName = stage == .compile ? "Build" : "Ingest"
        switch run.outcome {
        case .failed: return "\(stageName) failed"
        case .capped: return "\(stageName) stopped early"
        case .partial, .ok, .stopped: return "\(stageName) incomplete"
        }
    }

    /// Failure and partial endings differ in shape, not only color.
    var symbolName: String {
        run.outcome == .failed ? "xmark.octagon.fill" : "exclamationmark.triangle.fill"
    }

    var tint: Color {
        run.outcome == .failed ? Theme.Colors.danger : Theme.Colors.accentAmber
    }

}
