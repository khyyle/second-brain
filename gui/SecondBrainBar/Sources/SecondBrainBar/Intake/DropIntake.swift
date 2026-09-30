import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// Files just added to the vault, confirmed in the drop card.
struct AddedFiles: Equatable, Identifiable {
    let id = UUID()
    let count: Int
    var exportDescription: String? = nil
}

/// The app's way in for files. It copies dropped or picked files into the drop
/// folders and holds the outcome the drop card shows. One instance serves the
/// whole window, so files dropped on any tab land the same way as files
/// dropped on the card.
@MainActor
final class DropIntake: ObservableObject {
    /// Why a drop or import added nothing. `detail` falls back to the card's
    /// accepted-types hint.
    struct Failure: Equatable {
        let title: String
        var detail: String? = nil
    }

    /// Whether files are being dragged over the window, and if so whether they
    /// will be taken.
    enum Drag {
        case none
        case accepting
        case rejecting
    }

    @Published private(set) var drag = Drag.none
    @Published private(set) var added: AddedFiles?
    @Published private(set) var failure: Failure?
    @Published private(set) var isAdding = false
    @Published private(set) var addingIsSlow = false
    /// Counts every batch of files handed in by drop or picker, so the window
    /// can bring the Ingest tab forward to show them arriving.
    @Published private(set) var submissionCount = 0
    /// Off while a screen that shouldn't be interrupted (Settings) is showing.
    var acceptsFiles = true

    private static let addedVisibleSeconds: TimeInterval = 1.5
    private static let failureVisibleSeconds: TimeInterval = 3
    /// Copies that finish sooner than this show no progress, so a quick drop
    /// never flashes a spinner.
    private static let slowAddingSeconds: TimeInterval = 0.3

    private let config: AppConfig
    private var addedDismissal: DispatchWorkItem?
    private var failureDismissal: DispatchWorkItem?
    private var slowAddingTimer: DispatchWorkItem?

    init(config: AppConfig) {
        self.config = config
    }

    // MARK: - Dragging

    /// Record a drag entering the window and report whether its files will
    /// be taken.
    func dragEntered(_ urls: [URL]) -> Bool {
        guard acceptsFiles else { return false }
        let acceptable = Self.accepts(urls)
        clearFailure()
        drag = acceptable ? .accepting : .rejecting
        return acceptable
    }

    func dragEnded() {
        drag = .none
    }

    /// Whether a dragged selection is worth taking: documents, folders that may
    /// hold some, or a JSON file that may be a conversation export. What is
    /// actually added is decided once the files are dropped.
    private static func accepts(_ urls: [URL]) -> Bool {
        urls.contains { url in
            if (try? url.resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory == true {
                return true
            }
            let fileExtension = url.pathExtension.lowercased()
            return fileExtension == "json" || DropStaging.documentExtensions.contains(fileExtension)
        }
    }

    // MARK: - Pickers

    func presentAddFilesPanel() {
        guard acceptsFiles else { return }
        let panel = makePanel(prompt: "Add", message: "Choose files or folders to add to Second Brain")
        panel.allowedContentTypes = DropStaging.documentExtensions.union(["json"])
            .compactMap { UTType(filenameExtension: $0) }
        if panel.runModal() == .OK { add(panel.urls) }
    }

    /// A file/folder open panel. The app is a menu-bar accessory, so it must
    /// activate first or the panel opens behind everything.
    private func makePanel(prompt: String, message: String) -> NSOpenPanel {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = true
        panel.prompt = prompt
        panel.message = message
        panel.level = .modalPanel
        NSApp.activate(ignoringOtherApps: true)
        return panel
    }

    // MARK: - Adding

    /// Copy document files into the documents lane. A conversation export found
    /// in the selection waits for confirmation that names it and its conversation
    /// count, then goes to its provider's lane. A folder holding an export
    /// contributes only the export; its attachments are not staged as documents.
    func add(_ urls: [URL]) {
        guard !urls.isEmpty else { return }
        submissionCount += 1
        beginAdding()
        let config = config
        DispatchQueue.global(qos: .userInitiated).async {
            let export = ExportProvider.detect(in: urls)
            let exportFolderPaths = Set(export?.files.map { $0.deletingLastPathComponent().path } ?? [])
            let result = DropStaging.stageDocuments(
                urls.filter { !exportFolderPaths.contains($0.path) }, config: config
            )
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                if let export, self.confirmImport(of: export) {
                    self.copyExport(export, documentsAdded: result.added)
                } else {
                    self.endAdding()
                    self.reportDocuments(result, declinedExport: export != nil)
                }
            }
        }
    }

    private func copyExport(_ export: DetectedExport, documentsAdded: Int) {
        let config = config
        DispatchQueue.global(qos: .userInitiated).async {
            let exportCopied = DropStaging.importExport(export.provider, files: export.files, config: config)
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.endAdding()
                if exportCopied > 0 {
                    self.confirm(
                        count: documentsAdded + exportCopied,
                        exportDescription: "\(export.provider.displayName) export: \(export.sourceName)"
                    )
                } else if documentsAdded > 0 {
                    self.confirm(count: documentsAdded)
                } else {
                    self.fail(Failure(title: "Couldn't import the export", detail: "Try again"))
                }
            }
        }
    }

    /// A declined export is the person's choice, so it isn't reported as a
    /// failure even when nothing else was added.
    private func reportDocuments(_ result: (added: Int, failed: Bool), declinedExport: Bool) {
        if result.added > 0 {
            confirm(count: result.added)
        } else if declinedExport {
            return
        } else if result.failed {
            fail(Failure(title: "Couldn't copy the files", detail: "Try again"))
        } else {
            fail(Failure(title: "No supported files found"))
        }
    }

    /// The app is a menu-bar accessory, so it must activate first or the alert
    /// opens behind everything.
    private func confirmImport(of export: DetectedExport) -> Bool {
        NSApp.activate(ignoringOtherApps: true)
        let count = export.conversationCount
        let unit = count == 1 ? "conversation" : "conversations"
        let alert = NSAlert()
        alert.messageText = "Import \(count) \(unit) from \(export.sourceName)?"
        alert.addButton(withTitle: "Import")
        alert.addButton(withTitle: "Cancel")
        return alert.runModal() == .alertFirstButtonReturn
    }

    // MARK: - Outcomes

    private func beginAdding() {
        guard !isAdding else { return }
        isAdding = true
        let timer = DispatchWorkItem { [weak self] in self?.addingIsSlow = true }
        slowAddingTimer = timer
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.slowAddingSeconds, execute: timer)
    }

    private func endAdding() {
        slowAddingTimer?.cancel()
        slowAddingTimer = nil
        isAdding = false
        addingIsSlow = false
    }

    private func confirm(count: Int, exportDescription: String? = nil) {
        clearFailure()
        addedDismissal?.cancel()
        added = AddedFiles(count: count, exportDescription: exportDescription)
        playPing()
        let dismissal = DispatchWorkItem { [weak self] in self?.added = nil }
        addedDismissal = dismissal
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.addedVisibleSeconds, execute: dismissal)
    }

    private func fail(_ newFailure: Failure) {
        failureDismissal?.cancel()
        failure = newFailure
        let dismissal = DispatchWorkItem { [weak self] in self?.failure = nil }
        failureDismissal = dismissal
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.failureVisibleSeconds, execute: dismissal)
    }

    private func clearFailure() {
        failureDismissal?.cancel()
        failureDismissal = nil
        failure = nil
    }

    /// Soft, brief confirmation sound (a gentle "pop", not the flat no-action
    /// Tink). Falls back silently if unavailable.
    private func playPing() {
        if let sound = NSSound(named: NSSound.Name("Pop")) {
            sound.volume = 0.35
            sound.play()
        }
    }
}
