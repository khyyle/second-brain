import Foundation

/// Debounced auto-run of the pipeline after files are dropped.
///
/// A bulk drop arrives as many separate copies; debouncing collapses
/// them into a single pipeline run that fires a few seconds after the
/// last file lands. Does nothing if the pipeline script is unavailable.
///
/// `schedule()` is expected to be called on the main queue (from the UI or a
/// main-dispatched watcher callback). It owns no cross-thread state of its own.
final class AutoRunner {
    private let config: AppConfig
    private var work: DispatchWorkItem?
    private let debounce: TimeInterval = 4

    init(config: AppConfig) {
        self.config = config
    }

    /// Debounced ingest of the drop folders after files are added, resetting
    /// the timer if called again within the window. Never compiles.
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
