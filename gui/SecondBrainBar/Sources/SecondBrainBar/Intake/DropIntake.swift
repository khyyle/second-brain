import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// Files just added to the vault, confirmed in the drop card.
struct AddedFiles: Equatable, Identifiable {
    let id = UUID()
    let count: Int
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

    /// A conversation export found in a drop, offered for one-click import.
    struct DetectedExport {
        let provider: ExportProvider
        let files: [URL]
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
    @Published private(set) var pendingExport: DetectedExport?
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
        panel.allowedContentTypes = DropStaging.documentExtensions.compactMap { UTType(filenameExtension: $0) }
        if panel.runModal() == .OK { add(panel.urls) }
    }

    func presentImportPanel() {
        let panel = makePanel(
            prompt: "Import",
            message: "Choose a ChatGPT export: its conversations.json file or the unzipped export folder"
        )
        panel.allowedContentTypes = [.json]
        guard panel.runModal() == .OK else { return }
        let selection = panel.urls
        beginAdding()
        DispatchQueue.global(qos: .userInitiated).async {
            let files = ExportProvider.chatgpt.exportFiles(in: selection)
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                if files.isEmpty {
                    self.endAdding()
                    self.fail(Failure(
                        title: "Not a ChatGPT export",
                        detail: "Choose conversations.json or the export folder"
                    ))
                } else {
                    self.copyExport(.chatgpt, files: files)
                }
            }
        }
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

    /// Copy document files into the documents lane, and if the selection also
    /// holds a conversation export, surface it for one-click import rather
    /// than dropping it silently.
    func add(_ urls: [URL]) {
        guard !urls.isEmpty else { return }
        submissionCount += 1
        beginAdding()
        let config = config
        DispatchQueue.global(qos: .userInitiated).async {
            let result = DropStaging.stageDocuments(urls, config: config)
            let export = ExportProvider.detect(in: urls).map {
                DetectedExport(provider: $0, files: $0.exportFiles(in: urls))
            }
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.endAdding()
                self.pendingExport = export
                if result.added > 0 {
                    self.confirm(count: result.added)
                } else if export != nil {
                    self.clearFailure()
                } else if result.failed {
                    self.fail(Failure(title: "Couldn't copy the files", detail: "Try again"))
                } else {
                    self.fail(Failure(title: "No supported files found"))
                }
            }
        }
    }

    func importPending() {
        guard let pending = pendingExport else { return }
        pendingExport = nil
        copyExport(pending.provider, files: pending.files)
    }

    private func copyExport(_ provider: ExportProvider, files: [URL]) {
        beginAdding()
        let config = config
        DispatchQueue.global(qos: .userInitiated).async {
            let added = DropStaging.importExport(provider, files: files, config: config)
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.endAdding()
                if added > 0 {
                    self.confirm(count: added)
                } else {
                    self.fail(Failure(title: "Couldn't import the export", detail: "Try again"))
                }
            }
        }
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

    private func confirm(count: Int) {
        clearFailure()
        addedDismissal?.cancel()
        added = AddedFiles(count: count)
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
