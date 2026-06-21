import Foundation

/// Debounced auto-run of the pipeline after files are dropped.
///
/// A bulk drop arrives as many separate copies; debouncing collapses
/// them into a single pipeline run that fires a few seconds after the
/// last file lands. Does nothing if the pipeline script is unavailable.
@MainActor
final class AutoRunner {
    private let config: AppConfig
    private var work: DispatchWorkItem?
    private let debounce: TimeInterval = 4

    init(config: AppConfig) {
        self.config = config
    }

    /// Schedule a free ingest run, resetting the timer if called again
    /// within the debounce window. Scans only the drop folders, not watched
    /// folders, so an interactive drop ingests just what was added. Only
    /// ingestion is auto-run; the paid compile step is always explicit.
    func schedule() {
        guard let script = config.runScriptPath else { return }
        work?.cancel()
        let item = DispatchWorkItem {
            PipelineRunner.runDetached(scriptURL: script, stage: .drops)
        }
        work = item
        DispatchQueue.main.asyncAfter(deadline: .now() + debounce, execute: item)
    }
}
