import AppKit

/// Open a file in its default app — markdown opens in Obsidian or an editor,
/// so a row click reads the underlying source or page.
func openInDefaultApp(_ url: URL) {
    NSWorkspace.shared.open(url)
}
