import CoreServices
import Foundation

/// Reports changes in the drop queue and watched folders while the app runs.
/// App-created files are ignored because interactive imports already schedule
/// ingestion. Scheduled scans cover files that arrive while the app is closed.
final class FolderWatcher {
    private let directories: [URL]
    private let onChange: () -> Void
    private var stream: FSEventStreamRef?

    /// - Parameters:
    ///   - directories: existing folders to watch recursively.
    ///   - onChange: invoked on the main queue after a change is observed.
    init(directories: [URL], onChange: @escaping () -> Void) {
        self.directories = directories
        self.onChange = onChange
    }

    /// Begin watching; a no-op if already started.
    func start() {
        guard stream == nil else { return }
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
            let watcher = Unmanaged<FolderWatcher>.fromOpaque(info).takeUnretainedValue()
            DispatchQueue.main.async { watcher.onChange() }
        }
        let flags = UInt32(
            kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagIgnoreSelf
        )
        guard let stream = FSEventStreamCreate(
            kCFAllocatorDefault,
            callback,
            &context,
            directories.map(\.path) as CFArray,
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
