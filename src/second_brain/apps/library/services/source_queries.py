"""Paginated source lookup by ingestion status and input path."""

from second_brain.apps.library.errors import SourceNotFoundError
from second_brain.apps.library.models import Source


def list_sources(
    *,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Source], int]:
    """Read a page of source records, ordered newest first with stable path tie-breaking.

    Parameters
    ----------
    status: str | None
        Exact ingestion status to filter by, or None to include all statuses.
    limit: int
        Maximum records to return. The API validates a range of 1 through 200.
    offset: int
        Number of matching records to skip. The API requires a nonnegative value.

    Returns
    -------
    tuple[list[Source], int]
        The requested page and the total matching count before pagination. An empty
        page is valid, including when offset is past the last matching record.

    """
    sources = Source.objects.all()
    if status is not None:
        sources = sources.filter(status=status)

    total = sources.count()
    # Break timestamp ties by primary key so pagination has a deterministic order.
    items = list(sources.order_by("-updated_at", "file_path")[offset : offset + limit])
    return items, total


def get_source(file_path: str) -> Source:
    """Look up a source's database record by its stored primary key.

    Parameters
    ----------
    file_path: str
        Source.file_path primary key, usually an absolute input path. This differs
        from raw_path, which identifies parsed Markdown relative to raw/.

    Returns
    -------
    Source
        The stored ingestion record, including status and the parsed-output path.

    Raises
    ------
    SourceNotFoundError
        No Source row has the supplied file_path.

    Notes
    -----
    Catch only DoesNotExist: database failures and programming errors must propagate.
    """
    try:
        return Source.objects.get(pk=file_path)
    except Source.DoesNotExist as exc:
        raise SourceNotFoundError(
            "This source is no longer in the library. Refresh and try again."
        ) from exc
