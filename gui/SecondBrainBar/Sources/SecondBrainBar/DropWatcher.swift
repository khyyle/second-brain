import CoreServices
import Foundation

/// Watches the vault's `drops/` folder and reports changes, so a file that
/// lands there by any means — a Finder copy, an `mcp` capture, a script — is
/// ingested while the app is running, not only files added through the app's
/// own drop zone. Events from this process are ignored (the app already
/// schedules ingest for its own drops); the scheduled run remains the backstop
/// for whatever arrives while the app is closed.
final class DropWatcher {
    private let directory: URL
    private let onChange: () -> Void
    private var stream: FSEventStreamRef?

    /// - Parameters:
    ///   - directory: folder to watch, recursively.
    ///   - onChange: invoked on the main queue after a change is observed.
    init(directory: URL, onChange: @escaping () -> Void) {
        self.directory = directory
        self.onChange = onChange
    }

    /// Begin watching; a no-op if already started.
    func start() {
        guard stream == nil else { return }
        try? FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true
        )

        var context = FSEventStreamContext(
            version: 0,
            info: Unmanaged.passUnretained(self).toOpaque(),
            retain: nil,
            release: nil,
            copyDescription: nil
        )
        // No captures, so this assigns cleanly to the C-convention callback type.
        let callback: FSEventStreamCallback = { _, info, _, _, _, _ in
            guard let info else { return }
            let watcher = Unmanaged<DropWatcher>.fromOpaque(info).takeUnretainedValue()
            DispatchQueue.main.async { watcher.onChange() }
        }
        let flags = UInt32(
            kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagIgnoreSelf
        )
        guard let stream = FSEventStreamCreate(
            kCFAllocatorDefault,
            callback,
            &context,
            [directory.path] as CFArray,
            FSEventStreamEventId(kFSEventStreamEventIdSinceNow),
            1.0,  // coalesce a burst of events from one drop into a single callback
            flags
        ) else { return }

        FSEventStreamSetDispatchQueue(stream, DispatchQueue.global(qos: .utility))
        FSEventStreamStart(stream)
        self.stream = stream
    }

    func stop() {
        guard let stream else { return }
        FSEventStreamStop(stream)
        FSEventStreamInvalidate(stream)
        FSEventStreamRelease(stream)
        self.stream = nil
    }

    deinit { stop() }
}
