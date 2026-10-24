"""Determine which source versions are staged or set aside, without running a build."""

from pathlib import Path

from second_brain.apps.compilation.services.build_records import BuildStore, DeferredSource
from second_brain.file_hashing import hash_file
from second_brain.pipeline_config import PipelineConfig


def _raw_sources(raw_dir: Path) -> list[str]:
    """Relative paths of every raw markdown source, outside hidden folders such as
    the skipped holding folder."""
    if not raw_dir.exists():
        return []
    sources: list[str] = []
    for md_file in raw_dir.rglob("*.md"):
        relative = md_file.relative_to(raw_dir)
        if not any(part.startswith(".") for part in relative.parts):
            sources.append(str(relative))
    return sorted(sources)


def _record_is_current(recorded_hash: str | None, raw_file: Path) -> bool:
    # Records made before hashes were kept have none; trust them rather than
    # paying to rebuild every source.
    return recorded_hash is None or recorded_hash == hash_file(raw_file)


def find_new_sources(config: PipelineConfig) -> list[str]:
    """
    Find raw source files that haven't been compiled in their current version.

    A source whose file changed since it was built is staged again. Set-aside
    sources (a prior run ended without completing) are excluded until their
    file changes or the user explicitly restages them.

    Parameters
    ----------
    config: PipelineConfig
        Application configuration.

    Returns
    -------
    list[str]
        Sorted relative paths of raw sources to compile.
    """
    raw_dir = config.raw_dir
    build_store = BuildStore()
    compiled_hashes = build_store.get_compiled()
    deferred_sources = build_store.get_deferred()

    new_sources: list[str] = []
    for rel in _raw_sources(raw_dir):
        raw_file = raw_dir / rel
        if rel in deferred_sources and _record_is_current(
            deferred_sources[rel].content_hash, raw_file
        ):
            continue
        if rel in compiled_hashes and _record_is_current(compiled_hashes[rel], raw_file):
            continue
        new_sources.append(rel)
    return new_sources


def find_set_aside_sources(config: PipelineConfig) -> dict[str, DeferredSource]:
    """
    Find set-aside sources whose file is still the version that was set aside.

    Parameters
    ----------
    config: PipelineConfig
        Application configuration.

    Returns
    -------
    dict[str, DeferredSource]
        Relative raw path mapped to why the build set it aside.
    """
    raw_dir = config.raw_dir
    return {
        rel: deferred
        for rel, deferred in BuildStore().get_deferred().items()
        if (raw_dir / rel).exists() and _record_is_current(deferred.content_hash, raw_dir / rel)
    }
