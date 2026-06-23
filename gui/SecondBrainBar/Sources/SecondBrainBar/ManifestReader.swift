import Foundation
import SQLite3

/// One row of recent activity, denormalised for display.
struct ManifestRow: Identifiable, Hashable {
    let id: String           // file_path doubles as primary key
    let displayName: String  // last path component
    let sourceType: String
    let status: Status
    let updatedAt: Date

    enum Status: String {
        case pending
        case processing
        case complete
        case failed
        case unknown
    }
}

/// Read-only view of the Python-side `manifest.db`.
///
/// Re-opens the database on every read so we always see the latest
/// committed state, which matters because the Python pipeline writes
/// to the same file from a separate process. SQLite's file locking
/// handles concurrent reads safely.
struct ManifestReader {
    let dbPath: URL

    func recent(limit: Int = 8) -> [ManifestRow] {
        guard FileManager.default.fileExists(atPath: dbPath.path) else { return [] }

        var db: OpaquePointer?
        guard sqlite3_open_v2(dbPath.path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            sqlite3_close(db)
            return []
        }
        defer { sqlite3_close(db) }

        let sql = """
            SELECT file_path, source_type, status, updated_at
            FROM manifest
            ORDER BY updated_at DESC
            LIMIT ?
        """

        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            return []
        }
        defer { sqlite3_finalize(stmt) }

        sqlite3_bind_int(stmt, 1, Int32(limit))

        var rows: [ManifestRow] = []
        let isoFormatter = ISO8601DateFormatter()
        isoFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

        while sqlite3_step(stmt) == SQLITE_ROW {
            let path   = sqlString(stmt, 0) ?? ""
            let source = sqlString(stmt, 1) ?? "unknown"
            let status = ManifestRow.Status(rawValue: sqlString(stmt, 2) ?? "")
                ?? .unknown
            let updatedRaw = sqlString(stmt, 3) ?? ""
            let updatedDate = isoFormatter.date(from: updatedRaw)
                ?? ISO8601DateFormatter().date(from: updatedRaw)
                ?? Date.distantPast

            rows.append(
                ManifestRow(
                    id: path,
                    displayName: (path as NSString).lastPathComponent,
                    sourceType: source,
                    status: status,
                    updatedAt: updatedDate
                )
            )
        }
        return rows
    }

    /// Status + last-update time for one source path.
    struct StatusInfo {
        let status: ManifestRow.Status
        let updatedAt: Date
    }

    /// Map of source file path to its status and update time, for
    /// classifying which dropped files are still queued and how long a
    /// processing item has been running.
    func statusByPath() -> [String: StatusInfo] {
        guard FileManager.default.fileExists(atPath: dbPath.path) else { return [:] }
        var db: OpaquePointer?
        guard sqlite3_open_v2(dbPath.path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            sqlite3_close(db)
            return [:]
        }
        defer { sqlite3_close(db) }

        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(
            db, "SELECT file_path, status, updated_at FROM manifest", -1, &stmt, nil
        ) == SQLITE_OK else { return [:] }
        defer { sqlite3_finalize(stmt) }

        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var out: [String: StatusInfo] = [:]
        while sqlite3_step(stmt) == SQLITE_ROW {
            let path = sqlString(stmt, 0) ?? ""
            let status = ManifestRow.Status(rawValue: sqlString(stmt, 1) ?? "") ?? .unknown
            let updated = (sqlString(stmt, 2)).flatMap { iso.date(from: $0) } ?? Date()
            out[path] = StatusInfo(status: status, updatedAt: updated)
        }
        return out
    }

    /// All recorded triage decisions, most recent first.
    func triageDecisions() -> [TriageRow] {
        guard FileManager.default.fileExists(atPath: dbPath.path) else { return [] }
        var db: OpaquePointer?
        guard sqlite3_open_v2(dbPath.path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            sqlite3_close(db)
            return []
        }
        defer { sqlite3_close(db) }

        // rowid breaks ties so a bulk run (near-identical timestamps) still
        // shows the most recently decided first.
        let sql = """
            SELECT raw_path, decision, confidence, reason, triaged_at
            FROM triage ORDER BY triaged_at DESC, rowid DESC
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }

        var rows: [TriageRow] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let rawPath = sqlString(stmt, 0) ?? ""
            // Skip rows that aren't chat-curation verdicts (e.g. the "deferred"
            // capacity state), so they don't read as triage decisions.
            guard let decision = TriageRow.Decision(rawValue: sqlString(stmt, 1) ?? "") else {
                continue
            }
            let confidence = sqlite3_column_double(stmt, 2)
            let reason = sqlString(stmt, 3) ?? ""
            rows.append(
                TriageRow(
                    id: rawPath,
                    displayName: (rawPath as NSString).lastPathComponent,
                    decision: decision,
                    confidence: confidence,
                    reason: reason
                )
            )
        }
        return rows
    }

    /// Count of successfully ingested sources.
    func completeCount() -> Int {
        guard FileManager.default.fileExists(atPath: dbPath.path) else { return 0 }
        var db: OpaquePointer?
        guard sqlite3_open_v2(dbPath.path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            sqlite3_close(db)
            return 0
        }
        defer { sqlite3_close(db) }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(
            db, "SELECT COUNT(*) FROM manifest WHERE status = 'complete'", -1, &stmt, nil
        ) == SQLITE_OK else { return 0 }
        defer { sqlite3_finalize(stmt) }
        return sqlite3_step(stmt) == SQLITE_ROW ? Int(sqlite3_column_int(stmt, 0)) : 0
    }

    /// Relative raw paths the compiler has already turned into wiki pages.
    func compiledRawPaths() -> Set<String> {
        readStrings("SELECT raw_path FROM compiled")
    }

    /// Map of relative raw path to its triage decision string.
    func triageDecisionMap() -> [String: String] {
        guard FileManager.default.fileExists(atPath: dbPath.path) else { return [:] }
        var db: OpaquePointer?
        guard sqlite3_open_v2(dbPath.path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            sqlite3_close(db)
            return [:]
        }
        defer { sqlite3_close(db) }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, "SELECT raw_path, decision FROM triage", -1, &stmt, nil)
            == SQLITE_OK else { return [:] }
        defer { sqlite3_finalize(stmt) }
        var out: [String: String] = [:]
        while sqlite3_step(stmt) == SQLITE_ROW {
            if let k = sqlString(stmt, 0) { out[k] = sqlString(stmt, 1) ?? "" }
        }
        return out
    }

    private func readStrings(_ sql: String) -> Set<String> {
        guard FileManager.default.fileExists(atPath: dbPath.path) else { return [] }
        var db: OpaquePointer?
        guard sqlite3_open_v2(dbPath.path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            sqlite3_close(db)
            return []
        }
        defer { sqlite3_close(db) }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        var out: Set<String> = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            if let s = sqlString(stmt, 0) { out.insert(s) }
        }
        return out
    }

    private func sqlString(_ stmt: OpaquePointer?, _ col: Int32) -> String? {
        guard let raw = sqlite3_column_text(stmt, col) else { return nil }
        return String(cString: raw)
    }
}
