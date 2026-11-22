"""Wiki search, domain edits, and Git maintenance commands."""

import click

from .common import json_output, print_result, request


@click.command()
@click.argument("question")
@json_output
@click.pass_context
def query(context: click.Context, question: str, as_json: bool) -> None:
    """Search the wiki for QUESTION."""
    result = request(context, "GET", "/wiki/search", params={"q": question})
    lines = [f"{hit['title']} ({hit['path']})\n{hit['snippet']}" for hit in result]
    print_result(result, as_json, "\n\n".join(lines) or "No results found.")


@click.command()
@json_output
@click.pass_context
def health(context: click.Context, as_json: bool) -> None:
    """Check wiki links, metadata, page sizes, and duplicate suggestions."""
    result = request(context, "GET", "/wiki/health")
    lines = []
    for category, items in result.items():
        if items:
            lines.append(category.replace("_", " ").capitalize() + ":")
            lines.extend(
                "  " + (str(item) if isinstance(item, str) else str(tuple(item))) for item in items
            )
    print_result(result, as_json, "\n".join(lines) or "No wiki issues found.")


@click.group()
def domain() -> None:
    """View and edit the wiki's domain vocabulary."""


@domain.command("list")
@json_output
@click.pass_context
def domain_list(context: click.Context, as_json: bool) -> None:
    """List domains with their page counts."""
    result = request(context, "GET", "/domains")
    lines = [f"{item['name']} ({item['page_count']})" for item in result]
    print_result(result, as_json, "\n".join(lines) or "No domains yet.")


@domain.command("rename")
@click.argument("old")
@click.argument("new")
@click.pass_context
def domain_rename(context: click.Context, old: str, new: str) -> None:
    """Rename OLD to NEW across every page and the schema."""
    result = request(context, "POST", "/domains/rename", {"old": old, "new": new})
    click.echo(f"Renamed '{old}' to '{new}' across {result['changed_pages']} page(s).")


@domain.command("merge")
@click.argument("dest")
@click.argument("sources", nargs=-1, required=True)
@click.pass_context
def domain_merge(context: click.Context, dest: str, sources: tuple[str, ...]) -> None:
    """Merge one or more SOURCES domains into DEST."""
    result = request(context, "POST", "/domains/merge", {"dest": dest, "sources": list(sources)})
    click.echo(
        f"Merged {', '.join(sources)} into '{dest}' across {result['changed_pages']} page(s)."
    )


@domain.command("delete")
@click.argument("name")
@click.pass_context
def domain_delete(context: click.Context, name: str) -> None:
    """Remove NAME from page metadata and the schema, preserving the pages."""
    result = request(context, "DELETE", "/domains", params={"name": name})
    click.echo(f"Deleted '{name}' from {result['changed_pages']} page(s).")


@click.group()
def wiki() -> None:
    """Inspect and repair the compiled wiki repository."""


@wiki.command("log")
@click.option("-n", "--limit", type=click.IntRange(1, 200), default=10, show_default=True)
@json_output
@click.pass_context
def wiki_log(context: click.Context, limit: int, as_json: bool) -> None:
    """List recent wiki checkpoints."""
    result = request(context, "GET", "/wiki/history", params={"limit": limit})
    lines = [
        f"{item['short_hash']}  {item['date']}  "
        f"{item['files_changed']:>4} file(s)  {item['subject']}"
        for item in result
    ]
    print_result(result, as_json, "\n".join(lines) or "No wiki commits yet.")


@wiki.command("rollback")
@click.argument("count", type=click.IntRange(min=1), default=1)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
def wiki_rollback(context: click.Context, count: int, yes: bool) -> None:
    """Undo the last COUNT wiki commits and restage affected sources."""
    if not yes:
        click.confirm(
            f"Roll the wiki back {count} commit(s)? The affected pages are "
            "discarded and their sources recompile on the next build.",
            abort=True,
        )
    result = request(context, "POST", "/wiki/rollback", {"count": count})
    click.echo(f"Rolled back {len(result['reverted_commits'])} commit(s).")


@wiki.command("repair-links")
@click.option("--dry-run", is_flag=True, help="Show what would change without writing.")
@json_output
@click.pass_context
def wiki_repair_links(context: click.Context, dry_run: bool, as_json: bool) -> None:
    """Repoint links to pages renamed outside the pipeline."""
    result = request(context, "POST", "/wiki/repair-links", {"dry_run": dry_run})
    total = sum(item["links_repointed"] for item in result)
    verb = "Would repoint" if dry_run else "Repointed"
    print_result(result, as_json, f"{verb} {total} link(s) across {len(result)} page(s).")


@wiki.command("merge")
@click.argument("dest")
@click.argument("sources", nargs=-1, required=True)
@click.pass_context
def wiki_merge(context: click.Context, dest: str, sources: tuple[str, ...]) -> None:
    """Retire duplicate SOURCES into DEST; move any prose worth keeping there first."""
    result = request(context, "POST", "/wiki/merge", {"dest": dest, "sources": list(sources)})
    total = sum(item["links_repointed"] for item in result)
    click.echo(f"Merged {', '.join(sources)} into '{dest}'; repointed {total} link(s).")


@wiki.command("dismiss")
@click.argument("page_a")
@click.argument("page_b")
@click.pass_context
def wiki_dismiss(context: click.Context, page_a: str, page_b: str) -> None:
    """Mark PAGE_A and PAGE_B as not duplicates, hiding the suggestion."""
    request(context, "POST", "/wiki/dismissed-duplicates", {"page_a": page_a, "page_b": page_b})
    click.echo(f"Dismissed duplicate suggestion: {page_a} + {page_b}")
