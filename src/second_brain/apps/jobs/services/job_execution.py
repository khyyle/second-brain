"""Execute a claimed job with fixed inputs; model calls stay outside DB transactions."""

import json

from second_brain.apps.jobs.models import Job
from second_brain.apps.jobs.outcomes import StageOutcome
from second_brain.apps.jobs.services import job_progress
from second_brain.apps.preferences.services.user_settings import get_pipeline_config
from second_brain.django_config.paths import get_vault_paths
from second_brain.django_config.vault_storage import atomic_write, vault_lock
from second_brain.file_hashing import hash_file


def execute_job(job: Job) -> dict:
    """Dispatch one claimed attempt and return serializable results.

    Parameters
    ----------
    job: Job
        RUNNING attempt owned by this lane process. Credentials are read afresh from
        Keychain when compilation needs them and are never copied into the job.

    Returns
    -------
    dict
        Operation counts/outcome. Domain exceptions propagate to the worker's handler.
    """
    config = get_pipeline_config(job.config_snapshot)
    if job.kind == "ingest":
        from second_brain.apps.ingestion.services.ingestion_runs import run_ingestion

        return run_ingestion(config, job.payload)

    if job.kind == "index":
        from second_brain.mcp_server.search import SearchIndex

        index = SearchIndex(config.search_db_path, config.search)
        index.sync_from_wiki(config.wiki_dir)
        return {"embedded_pages": index.embed_pending() if config.search.semantic_enabled else 0}

    root = get_vault_paths().backend_dir / "jobs" / str(job.pk) / "raw"
    for relative, digest in job.payload["hashes"].items():
        if hash_file(root / relative) != digest:
            raise ValueError("Captured build inputs changed; submit a new build.")
    config = config.model_copy(update={"raw_input_dir": root})
    sources = job.payload["sources"]
    from second_brain.apps.compilation.services.clustering import preview

    if job.kind == "preview":
        started = job_progress.now_iso()

        def progress(done: int, total: int) -> None:
            job_progress.update_progress(
                phase="grouping", current=done, total=total, started_at=started
            )

        artifact = preview.build_preview(config, progress, sources=sources)
        if not job_progress.stop_requested():
            with vault_lock("grouping"):
                atomic_write(config.data_dir / preview.CLUSTERS_FILENAME, json.dumps(artifact))
                (config.data_dir / preview.OVERRIDES_FILENAME).unlink(missing_ok=True)
        return {"source_count": artifact["source_count"], "group_count": artifact["group_count"]}

    from second_brain.apps.compilation.services.build_curation import finalize_build_curation
    from second_brain.apps.compilation.services.build_records import BuildStore
    from second_brain.apps.compilation.services.clustering import (
        cluster_scoped_sources,
        get_clusterer,
    )
    from second_brain.apps.compilation.services.compiler import _split_oversized, run_compilation
    from second_brain.apps.wiki.services.wiki_queries import refresh_index

    units = job.payload.get("work_units")
    if units is None:
        units = (
            cluster_scoped_sources(
                sources,
                root,
                config.search,
                get_clusterer(config.clustering),
                config.clustering.sources,
                signature_chars=config.clustering.signature_chars,
            )
            if config.clustering.enabled
            else [[source] for source in sources]
        )
    # A queued build may follow an earlier build of the same captured versions.
    if not job.payload.get("full"):
        compiled = BuildStore().get_compiled()
        sources = [
            source for source in sources if compiled.get(source) != job.payload["hashes"][source]
        ]
        eligible = set(sources)
        units = [[source for source in unit if source in eligible] for unit in units]
        units = [unit for unit in units if unit]
    units = _split_oversized(units, config.clustering.max_sources_per_run)
    if job.payload.get("dry_run"):
        return {
            "planned_sources": sources,
            "work_units": units,
            "sources_compiled": 0,
            "outcome": "ok",
        }
    with vault_lock("wiki", blocking=False):
        result = run_compilation(
            config,
            dry_run=job.payload.get("dry_run", False),
            source_paths=sources,
            work_units=units,
        )
        refresh_index(config)
        if (
            result.get("outcome") == StageOutcome.OK
            and result.get("sources_compiled", 0) > 0
            and not job_progress.stop_requested()
        ):
            finalize_build_curation(job.payload.get("curation_version"))
    return result
