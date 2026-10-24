import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// Files just added to the vault, confirmed in the drop card.
struct AddedFiles: Equatable, Identifiable {
    let id = UUID()
    let count: Int
    let files: [QueueItem]
    var exportDescription: String? = nil
}

/// Submits selected files for import and holds pending confirmations and outcomes.
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
            return fileExtension == "json" || ImportSelection.documentExtensions.contains(fileExtension)
        }
    }

    // MARK: - Pickers

    func presentAddFilesPanel() {
        guard acceptsFiles else { return }
        let panel = makePanel(prompt: "Add", message: "Choose files or folders to add to Second Brain")
        panel.allowedContentTypes = ImportSelection.documentExtensions.union(["json"])
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

    /// Inspect the selection locally, then let the backend copy it and queue ingestion.
    func add(_ urls: [URL]) {
        guard !urls.isEmpty, !isAdding else { return }
        submissionCount += 1
        beginAdding()
        Task {
            defer { endAdding() }
            let selection = await Task.detached(priority: .userInitiated) {
                let export = ExportProvider.detect(in: urls)
                let folders = export?.exportFolders ?? []
                return (ImportSelection.documentFiles(in: urls.filter { !folders.contains($0) }), export)
            }.value
            var files = selection.0
            var description: String?
            if let export = selection.1, confirmImport(of: export) {
                files += export.files
                description = "\(export.provider.displayName) export: \(export.sourceName)"
            }
            guard !files.isEmpty else {
                if selection.1 == nil { fail(Failure(title: "No supported files found")) }
                return
            }
            do {
                let submission: ImportSubmission = try await BackendClient(config: config).request(
                    "/imports", method: "POST", body: ["paths": files.map(\.path), "source_type": "auto"])
                confirm(files: submission.files.map(\.queueItem), exportDescription: description)
            } catch {
                fail(Failure(title: "Couldn't import the files", detail: error.localizedDescription))
            }
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

    private func confirm(files: [QueueItem], exportDescription: String? = nil) {
        clearFailure()
        addedDismissal?.cancel()
        added = AddedFiles(count: files.count, files: files, exportDescription: exportDescription)
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
