import SwiftUI
import UniformTypeIdentifiers
import AppKit

/// The drop zone, styled as a card that highlights on hover or drag.
///
/// The card takes documents (PDFs, notes, folders of them). Conversation
/// exports are a separate, rare action behind an explicit "Import" affordance
/// beneath the card, because their formats are provider-specific and a
/// misrouted multi-gigabyte export is an expensive mistake. If an export is
/// dropped on the card anyway, it isn't silently mishandled: the import
/// affordance turns into a one-click prompt for it.
///
/// Dropped or picked files are copied (not moved) into the drop folders; the
/// Python watcher picks them up from there.
struct BinDropZone: View {
    let bin: Bin
    let config: AppConfig
    @ObservedObject var feedback: UploadFeedback

    private struct DetectedExport {
        let provider: ExportProvider
        let files: [URL]
    }

    @State private var isTargeted = false
    @State private var isHovering = false
    @State private var importHovering = false
    @State private var errorMessage: String?
    @State private var pendingExport: DetectedExport?

    var body: some View {
        VStack(spacing: 7) {
            dropCard
            importRow
        }
    }

    private var dropCard: some View {
        ZStack {
            RoundedRectangle(cornerRadius: Theme.Metric.corner, style: .continuous)
                .fill(fillColor)
            RoundedRectangle(cornerRadius: Theme.Metric.corner, style: .continuous)
                .strokeBorder(strokeColor, lineWidth: 1)

            VStack(spacing: 9) {
                Image(systemName: bin.iconName)
                    .font(.system(size: 22, weight: .regular))
                    .foregroundStyle(iconColor)
                VStack(spacing: 3) {
                    Text(errorMessage ?? bin.displayName)
                        .font(Theme.Font.body(12.5, weight: .medium))
                        .foregroundStyle(errorMessage == nil ? Theme.Colors.textPrimary : Theme.Colors.danger)
                    if errorMessage == nil {
                        Text(bin.hint)
                            .font(Theme.Font.body(10.5))
                            .foregroundStyle(Theme.Colors.textSecondary)
                    }
                }
                .lineLimit(1)
                .truncationMode(.middle)
                .padding(.horizontal, 10)
            }
        }
        .frame(height: Theme.Metric.zoneHeight)
        .contentShape(Rectangle())
        .onTapGesture { openDocumentPicker() }
        .onHover { isHovering = $0 }
        .animation(.easeInOut(duration: 0.14), value: isTargeted)
        .animation(.easeInOut(duration: 0.14), value: isHovering)
        .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
            handleDrop(providers: providers)
        }
        .help("Drop files or a folder here, or click to browse")
    }

    /// Deliberate "Import ChatGPT export…", which becomes a one-click prompt
    /// when an export was just dropped on the card by mistake.
    @ViewBuilder
    private var importRow: some View {
        if let pending = pendingExport {
            HStack(spacing: 6) {
                Image(systemName: "questionmark.circle")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.Colors.accentAmber)
                Text("Looks like a \(pending.provider.displayName) export")
                    .font(Theme.Font.meta(10.5))
                    .foregroundStyle(Theme.Colors.textSecondary)
                    .lineLimit(1).truncationMode(.tail)
                Spacer(minLength: 6)
                Button { importPending() } label: {
                    Text("Import")
                        .font(Theme.Font.meta(10).weight(.medium))
                        .foregroundStyle(Theme.Colors.accent)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Capsule().fill(Theme.Colors.accent.opacity(0.16)))
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 2)
        } else {
            HStack(spacing: 0) {
                Spacer(minLength: 0)
                Button { openImportPicker() } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "square.and.arrow.down")
                            .font(.system(size: 9, weight: .semibold))
                        Text("Import ChatGPT export")
                            .font(Theme.Font.meta(10))
                    }
                    .foregroundStyle(importHovering ? Theme.Colors.textSecondary : Theme.Colors.textTertiary)
                    .padding(.horizontal, 7).padding(.vertical, 3)
                    .background(
                        RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .fill(importHovering ? Theme.Colors.surfaceHover : Color.clear)
                    )
                }
                .buttonStyle(.plain)
                .onHover { importHovering = $0 }
                Spacer(minLength: 0)
            }
        }
    }

    private var fillColor: Color {
        (isTargeted || isHovering) ? Theme.Colors.surfaceHover : Theme.Colors.surface
    }

    private var strokeColor: Color {
        (isTargeted || isHovering) ? Theme.Colors.textTertiary : Theme.Colors.stroke
    }

    private var iconColor: Color {
        (isTargeted || isHovering) ? Theme.Colors.textPrimary : Theme.Colors.textSecondary
    }

    // MARK: - Pickers

    private func openDocumentPicker() {
        let panel = makePanel(prompt: "Add", message: "Choose files or folders to add to Second Brain")
        if panel.runModal() == .OK { stage(urls: panel.urls) }
    }

    private func openImportPicker() {
        let panel = makePanel(
            prompt: "Import",
            message: "Choose a ChatGPT export — its conversations.json or the unzipped export folder"
        )
        guard panel.runModal() == .OK else { return }
        let selection = panel.urls
        DispatchQueue.global(qos: .userInitiated).async {
            let files = ExportProvider.chatgpt.exportFiles(in: selection)
            DispatchQueue.main.async {
                if files.isEmpty {
                    errorMessage = "Not a ChatGPT export"
                } else {
                    runImport(provider: .chatgpt, files: files)
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

    // MARK: - Drop

    private func handleDrop(providers: [NSItemProvider]) -> Bool {
        let fileProviders = providers.filter {
            $0.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier)
        }
        guard !fileProviders.isEmpty else { return false }

        let group = DispatchGroup()
        var urls: [URL] = []
        let lock = NSLock()
        for provider in fileProviders {
            group.enter()
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                defer { group.leave() }
                if let url = resolveURL(item) {
                    lock.lock()
                    urls.append(url)
                    lock.unlock()
                }
            }
        }
        group.notify(queue: .main) { stage(urls: urls) }
        return true
    }

    private func resolveURL(_ item: NSSecureCoding?) -> URL? {
        if let url = item as? URL { return url }
        if let data = item as? Data { return URL(dataRepresentation: data, relativeTo: nil) }
        if let string = item as? String { return URL(string: string) }
        return nil
    }

    // MARK: - Staging

    /// Copy document files into the documents lane, and if the drop also
    /// contained a conversation export, surface it for one-click import
    /// rather than dropping it silently.
    private func stage(urls: [URL]) {
        guard !urls.isEmpty else { return }
        DispatchQueue.global(qos: .userInitiated).async {
            let result = DropStaging.stageDocuments(urls, config: config)
            let detected = ExportProvider.detect(in: urls)
            let export = detected.map { DetectedExport(provider: $0, files: $0.exportFiles(in: urls)) }
            DispatchQueue.main.async {
                pendingExport = export
                if result.added > 0 {
                    errorMessage = nil
                    feedback.confirm(count: result.added)
                } else if export != nil {
                    errorMessage = nil
                } else {
                    errorMessage = result.failed ? "Copy failed" : "No supported files found"
                }
            }
        }
    }

    private func importPending() {
        guard let pending = pendingExport else { return }
        pendingExport = nil
        runImport(provider: pending.provider, files: pending.files)
    }

    private func runImport(provider: ExportProvider, files: [URL]) {
        DispatchQueue.global(qos: .userInitiated).async {
            let added = DropStaging.importExport(provider, files: files, config: config)
            DispatchQueue.main.async {
                if added > 0 {
                    errorMessage = nil
                    feedback.confirm(count: added)
                } else {
                    errorMessage = "Import failed"
                }
            }
        }
    }
}
