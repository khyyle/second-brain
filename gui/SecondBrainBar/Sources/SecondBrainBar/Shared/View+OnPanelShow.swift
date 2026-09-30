import SwiftUI

extension Notification.Name {
    /// Posted when the menu-bar panel is brought to the front. The panel is
    /// reused across opens, so views that read from disk listen for this to
    /// refresh, since `.onAppear` only fires on the panel's first show.
    static let panelDidShow = Notification.Name("SecondBrainPanelDidShow")
}

extension View {
    /// Run `action` whenever the menu-bar panel is brought to the front. The
    /// panel is reused across opens, so `.onAppear` fires only once; disk-backed
    /// views pair this with their `.onAppear` refresh to reload on every reopen.
    func onPanelShow(_ action: @escaping () -> Void) -> some View {
        onReceive(NotificationCenter.default.publisher(for: .panelDidShow)) { _ in action() }
    }
}
