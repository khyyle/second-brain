import SwiftUI
import AppKit

/// Menu bar entry point.

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
    private var panel: NSPanel?
    private var store: PipelineStore?
    private let autoRunner = AutoRunner(config: AppConfig.default)
    private var dropWatcher: DropWatcher?

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(
            withLength: NSStatusItem.variableLength
        )
        if let button = statusItem.button {
            button.image = Self.statusBarIcon()
            button.toolTip = "Second Brain"
            button.action = #selector(statusItemClicked(_:))
            button.target = self
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }

        installMainMenu()

        // Ingest anything that lands in drops/ by any means, not just the
        // app's own drop zone, for as long as the app is running.
        let watcher = DropWatcher(directory: config.dropsRoot) { [weak self] in
            self?.autoRunner.schedule()
        }
        watcher.start()
        dropWatcher = watcher

        // Opening the app surfaces the window straight away; the status item
        // is the way back to it after that. Deferred briefly so the status
        // item is laid out and the first placement lands under its icon.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in
            self?.showPanel()
        }
    }

    /// Closing the panel only hides it; the status item brings it back. Quit
    /// is an explicit choice in the status item's menu, never a side effect of
    /// the close button.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    /// Re-opening the app (from Finder or Spotlight while it's already
    /// running) surfaces the panel again instead of doing nothing.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        showPanel()
        return true
    }

    @objc private func statusItemClicked(_ sender: AnyObject?) {
        if NSApp.currentEvent?.type == .rightMouseUp {
            showStatusMenu()
        } else {
            togglePanel()
        }
    }

    private func togglePanel() {
        if let panel, panel.isVisible {
            panel.orderOut(nil)
        } else {
            showPanel()
        }
    }

    /// Bring the panel up, creating it on first use. Only the first
    /// appearance is anchored under the status item, later shows keep
    /// wherever the user last dragged it.
    private func showPanel() {
        if let panel {
            NSApp.activate(ignoringOtherApps: true)
            panel.makeKeyAndOrderFront(nil)
        } else {
            let panel = makePanel()
            positionUnderStatusItem(panel)
            NSApp.activate(ignoringOtherApps: true)
            panel.makeKeyAndOrderFront(nil)
        }
    }

    private func showStatusMenu() {
        let menu = NSMenu()
        menu.addItem(
            withTitle: "Quit Second Brain",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )
        
        statusItem.menu = menu
        statusItem.button?.performClick(nil)
        statusItem.menu = nil
    }

    private func installMainMenu() {
        let mainMenu = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(
            withTitle: "Quit Second Brain",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )
        appItem.submenu = appMenu
        mainMenu.addItem(appItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = NSMenuItem(title: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(redo)
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        mainMenu.addItem(editItem)

        NSApp.mainMenu = mainMenu
    }

    private func makePanel() -> NSPanel {
        let store = PipelineStore(config: config)
        self.store = store
        let hosting = NSHostingController(
            rootView: ContentView(config: config).environmentObject(store)
        )
        hosting.sizingOptions = []

        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: Theme.Metric.popoverWidth, height: 420),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        panel.contentViewController = hosting
        panel.setContentSize(NSSize(width: Theme.Metric.popoverWidth, height: 420))
        panel.title = ""
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isMovableByWindowBackground = false
        panel.level = .normal
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.appearance = NSAppearance(named: .darkAqua)
        // Matches the top stop of the content's background gradient so the
        // title strip and the content read as one continuous surface.
        panel.backgroundColor = NSColor(Theme.Colors.panelChrome)
        panel.standardWindowButton(.miniaturizeButton)?.isHidden = true
        panel.standardWindowButton(.zoomButton)?.isHidden = true
        self.panel = panel
        return panel
    }

    /// Drop the panel just under the menu bar on the status item's screen.
    /// The vertical edge comes from the screen's visible frame (always just
    /// below the menu bar) rather than the status item's window, which has no
    /// reliable frame yet at launch and would otherwise push the panel
    /// off-screen. The horizontal centre tracks the icon once it's laid out,
    /// falling back to the menu-bar corner.
    private func positionUnderStatusItem(_ panel: NSPanel) {
        let screen = statusItem.button?.window?.screen ?? NSScreen.main
        guard let visible = screen?.visibleFrame else { return }
        let size = panel.frame.size

        var centerX = visible.maxX - size.width / 2 - 8
        if let button = statusItem.button, let window = button.window {
            let iconRect = window.convertToScreen(button.convert(button.bounds, to: nil))
            if iconRect.width > 1 { centerX = iconRect.midX }
        }

        let x = min(max(centerX - size.width / 2, visible.minX + 8), visible.maxX - size.width - 8)
        let y = visible.maxY - size.height - 4
        panel.setFrameOrigin(NSPoint(x: x, y: y))
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
