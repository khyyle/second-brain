import SwiftUI
import AppKit

/// Menu bar entry point.
///
/// We bypass `MenuBarExtra` and drive `NSStatusItem` + `NSPopover`
/// directly so we can set behavior to `.applicationDefined`. The
/// SwiftUI default and `.semitransient` both dismiss the popover on
/// any click outside the app, which kills the drag-from-Finder UX
/// (the user has to click Finder to bring it forward, which dismisses
/// the popover before the drag starts). With `.applicationDefined`
/// the popover stays open until the user clicks the status item
/// again or quits, which is predictable and drop-friendly.
@main
struct SecondBrainBarApp: App {
    @NSApplicationDelegateAdaptor(StatusBarController.self) private var controller

    var body: some Scene {
        Settings { EmptyView() }
    }
}

final class StatusBarController: NSObject, NSApplicationDelegate {
    private let config = AppConfig.default
    private var statusItem: NSStatusItem!
    private var popover: NSPopover!
    private let autoRunner = AutoRunner(config: AppConfig.default)
    private var dropWatcher: DropWatcher?

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(
            withLength: NSStatusItem.variableLength
        )
        if let button = statusItem.button {
            button.image = Self.statusBarIcon()
            button.toolTip = "Second Brain"
            button.action = #selector(togglePopover(_:))
            button.target = self
        }

        popover = NSPopover()
        popover.behavior = .applicationDefined
        popover.animates = true
        popover.contentSize = NSSize(width: Theme.Metric.popoverWidth, height: 420)
        popover.contentViewController = NSHostingController(
            rootView: ContentView(config: config)
        )

        // Ingest anything that lands in drops/ by any means, not just the
        // app's own drop zone, for as long as the app is running.
        let watcher = DropWatcher(directory: config.dropsRoot) { [weak self] in
            self?.autoRunner.schedule()
        }
        watcher.start()
        dropWatcher = watcher
    }

    @objc private func togglePopover(_ sender: AnyObject?) {
        guard let button = statusItem.button else { return }

        if popover.isShown {
            popover.performClose(sender)
        } else {
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
        }
    }

    private static let menuBarIconHeight: CGFloat = 27

    /// Menu bar glyph: the app logo's node-graph, cut out as a template so
    /// macOS tints it to the bar. Falls back to a matching SF Symbol when
    /// the bundled asset isn't present (e.g. running via `swift run`).
    private static func statusBarIcon() -> NSImage? {
        logoGlyph() ?? symbolGlyph()
    }

    private static func logoGlyph() -> NSImage? {
        guard let url = Bundle.main.url(forResource: "MenuBarIcon", withExtension: "png"),
              let source = NSImage(contentsOf: url), source.size.height > 0 else { return nil }
        let height = menuBarIconHeight
        let width = height * (source.size.width / source.size.height)
        let glyph = NSImage(size: NSSize(width: width, height: height))
        glyph.lockFocus()
        NSGraphicsContext.current?.imageInterpolation = .high
        source.draw(in: NSRect(x: 0, y: 0, width: width, height: height))
        glyph.unlockFocus()
        glyph.isTemplate = true
        return glyph
    }

    private static func symbolGlyph() -> NSImage? {
        let config = NSImage.SymbolConfiguration(pointSize: 15, weight: .regular)
        let candidates = [
            "point.3.connected.trianglepath.dotted",
            "point.topleft.down.to.point.bottomright.curvepath",
            "brain",
        ]
        for name in candidates {
            if let image = NSImage(systemSymbolName: name, accessibilityDescription: "Second Brain") {
                let configured = image.withSymbolConfiguration(config) ?? image
                configured.isTemplate = true
                return configured
            }
        }
        return nil
    }
}
