import Foundation

/// User-initiated domain vocabulary edits from the GUI.
///
/// Like ManifestMutator, the multi-file work (rewriting frontmatter across
/// pages, updating the schema, regenerating views and the search index) lives
/// in the Python CLI as the one source of truth; the GUI shells out and
/// refreshes when it finishes. The command runs off the main thread because a
/// rename touches every page that uses the domain.
enum DomainMutator {
    static func rename(
        config: AppConfig, old: String, new: String, completion: @escaping (Bool) -> Void
    ) {
        run(config, "domain rename \(quote(old)) \(quote(new))", completion: completion)
    }

    static func merge(
        config: AppConfig, sources: [String], dest: String, completion: @escaping (Bool) -> Void
    ) {
        let args = ([dest] + sources).map(quote).joined(separator: " ")
        run(config, "domain merge \(args)", completion: completion)
    }

    static func delete(config: AppConfig, name: String, completion: @escaping (Bool) -> Void) {
        run(config, "domain delete \(quote(name))", completion: completion)
    }

    private static func run(
        _ config: AppConfig, _ command: String, completion: @escaping (Bool) -> Void
    ) {
        guard let repo = config.repoDir else {
            completion(false)
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let ok = PipelineRunner.runManagedSync(repoDir: repo, command: command)
            DispatchQueue.main.async { completion(ok) }
        }
    }

    /// POSIX single-quote a CLI argument so names with shell metacharacters
    /// survive the shell-out (and embedded single quotes are escaped).
    private static func quote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}
