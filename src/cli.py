"""Command-line interface. Run via `python main.py <command>`."""
from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from . import config
from .collectors.manual_import import import_financials_csv, import_projects_csv, upsert_projects
from .database import db_manager
from .models import Project, ProjectStatus
from .seed_data import SEED_PROJECTS

console = Console()


@click.group()
def cli():
    """Nepal Hydropower Database."""


@cli.command()
@click.option("--drop", is_flag=True, help="Drop all existing tables first (DESTROYS DATA).")
def init(drop):
    """Create database tables."""
    if drop and not click.confirm("This deletes ALL data. Continue?"):
        return
    db_manager.init_db(drop=drop)
    console.print("[green]Database initialised.[/green]")


@cli.command("load-sample")
def load_sample():
    """Load reference data (districts, rivers, companies)."""
    db_manager.init_db()
    added = db_manager.init_reference_data()
    console.print(f"[green]Reference data loaded:[/green] {added}")


@cli.command("load-seed")
def load_seed():
    """Load the unverified seed project set (see src/seed_data.py)."""
    db_manager.init_db()
    db_manager.init_reference_data()
    with db_manager.session_scope() as s:
        result = upsert_projects(s, SEED_PROJECTS, default_reliability="seed-unverified")
    console.print(f"[green]Seed projects:[/green] {result['created']} created, {result['updated']} updated")
    for err in result["errors"]:
        console.print(f"[red]{err}[/red]")
    console.print("[yellow]Seed values are unverified: check against DoED/NEA before relying on them.[/yellow]")


cli.add_command(load_seed, "add-test")  # alias kept for the original docs


@cli.command()
def stats():
    """Show row counts per table."""
    table = Table(title="Database statistics")
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    counts = db_manager.get_db_stats()
    for name, n in sorted(counts.items()):
        table.add_row(name, str(n))
    console.print(table)


@cli.command()
@click.option("--status", type=click.Choice([s.value for s in ProjectStatus]), help="Filter by status.")
@click.option("--min-mw", type=float, help="Minimum capacity in MW.")
@click.option("--limit", default=50, show_default=True)
def query(status, min_mw, limit):
    """List projects."""
    stmt = select(Project).order_by(Project.capacity_mw.desc())
    if status:
        stmt = stmt.where(Project.status == ProjectStatus(status))
    if min_mw is not None:
        stmt = stmt.where(Project.capacity_mw >= min_mw)
    table = Table(title="Projects")
    for col in ("ID", "Name", "MW", "Status", "District", "Developer"):
        table.add_column(col)
    with db_manager.session_scope() as s:
        for p in s.scalars(stmt.limit(limit)):
            table.add_row(p.project_id, p.project_name_en, f"{p.capacity_mw:g}" if p.capacity_mw else "-",
                          p.status.value, p.district.district_name if p.district else "-",
                          p.developer.company_name if p.developer else "-")
    console.print(table)


@cli.command("import-csv")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--kind", type=click.Choice(["projects", "financials", "shareholders", "facts", "company-financials"]), default="projects",
              show_default=True)
def import_csv(path, kind):
    """Import projects, annual financials, shareholders or verified company facts from a CSV file."""
    from .collectors.manual_import import import_company_financials_csv, import_facts_csv, import_shareholders_csv
    importer = {"projects": import_projects_csv, "financials": import_financials_csv,
                "shareholders": import_shareholders_csv, "facts": import_facts_csv,
                "company-financials": import_company_financials_csv}[kind]
    db_manager.init_db()
    with db_manager.session_scope() as s:
        result = importer(s, path)
    console.print(f"[green]{kind}:[/green] {result['created']} created, {result['updated']} updated")
    for err in result["errors"]:
        console.print(f"[red]{err}[/red]")


@cli.command()
@click.option("--source", type=click.Choice(["all", "doed", "wikipedia", "nepse"]), default="all", show_default=True)
def sync(source):
    """Scrape DoED / Wikipedia / NEPSE listing and merge into the database (idempotent)."""
    from .collectors.sync import sync_records
    from .collectors.web_scraper import DoEDScraper, WikipediaScraper
    if not config.SCRAPING_ENABLED:
        raise click.ClickException("SCRAPING_ENABLED is false")
    db_manager.init_db()
    db_manager.init_reference_data()
    records: list[dict] = []
    if source in ("all", "doed"):
        with console.status("Scraping DoED..."):
            doed = DoEDScraper().scrape()
        console.print(f"DoED: {len(doed)} rows")
        records += doed
    if source in ("all", "wikipedia"):
        with console.status("Scraping Wikipedia..."):
            wiki = WikipediaScraper().scrape()
        console.print(f"Wikipedia: {len(wiki)} rows")
        records += wiki
    if records:
        with db_manager.session_scope() as s:
            result = sync_records(s, records)
        console.print(f"[green]Projects synced:[/green] {result.created} created, {result.updated} matched/updated, "
                      f"{len(result.conflicts)} conflicts")
        if result.conflicts:
            path = config.PROCESSED_DIR / "sync_conflicts.txt"
            path.write_text("\n".join(result.conflicts), encoding="utf-8")
            console.print(f"Conflicts written to {path}")
    if source in ("all", "nepse"):
        from .collectors.nepse_scraper import scrape_listed_hydropower, sync_listed_companies
        with console.status("Fetching NEPSE hydropower listing..."):
            listed = scrape_listed_hydropower()
        with db_manager.session_scope() as s:
            res = sync_listed_companies(s, listed)
        console.print(f"[green]NEPSE:[/green] {len(listed)} listed hydropower companies "
                      f"({res['matched']} matched existing, {res['created']} new)")


cli.add_command(sync, "scrape-doe")  # alias used in the original docs


@cli.command("update-news")
def update_news():
    """Fetch hydropower news from RSS feeds and attach it to mentioned projects."""
    from .collectors.news_scraper import attach_news, fetch_items
    db_manager.init_db()
    with console.status("Fetching feeds..."):
        items = fetch_items()
    with db_manager.session_scope() as s:
        result = attach_news(s, items)
    console.print(f"[green]News:[/green] {result['items']} relevant articles, "
                  f"{result['matched_items']} mention a known project, {result['updates_added']} updates added")


@cli.command()
@click.argument("dest", required=False, type=click.Path())
def backup(dest):
    """Copy the SQLite database to a timestamped backup file."""
    import shutil
    from datetime import datetime
    src = db_manager.engine.url.database
    if not src:
        raise click.ClickException("backup supports SQLite only")
    dest = dest or str(config.DATA_DIR / f"projects_backup_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(src, dest)
    console.print(f"[green]Backed up to[/green] {dest}")


@cli.command()
@click.option("--with-reports", is_flag=True, help="Also look for and download new report PDFs and extract facts (slow, uses disk).")
@click.option("--skip-network", is_flag=True, help="Skip every stage that needs the internet (backup, validate and export only).")
@click.pass_context
def refresh(ctx, with_reports, skip_network):
    """One-shot update for scheduling. Runs every stage even if one fails, then reports which did.

    Order: backup -> sync (DoED, Wikipedia, NEPSE) -> resolve-locations -> quarterly financials -> news -> validate -> export.
    The quarterly-financials stage stores each company's newest full report, so the multi-year trend tables grow with every run.
    """
    reports_cmds = cli.commands["reports"].commands
    stages = [("backup", "backup", {}, False)]
    stages += [("sync", "sync", {"source": "all"}, True), ("resolve-locations", "resolve-locations", {}, True),
               ("financials", reports_cmds["financials"], {}, True), ("update-news", "update-news", {}, True)]
    if with_reports:
        stages += [("reports discover", reports_cmds["discover"], {}, True), ("reports fetch (annual, then quarterly)", reports_cmds["fetch"], {"kind": "all"}, True),
                   ("reports extract", reports_cmds["extract"], {}, True)]
    stages += [("validate-data", "validate-data", {}, False), ("export", "export", {"kind": "master"}, False)]
    failed: list[tuple[str, str]] = []
    for label, command, kwargs, needs_network in stages:
        if needs_network and skip_network:
            console.print(f"[dim]skipped (no network): {label}[/dim]")
            continue
        console.rule(label)
        try:
            ctx.invoke(cli.commands[command] if isinstance(command, str) else command, **kwargs)
        except (Exception, SystemExit) as exc:  # one source being down must not stop the rest
            failed.append((label, str(exc) or type(exc).__name__))
            console.print(f"[red]{label} failed: {exc}[/red]")
    console.rule("summary")
    if failed:
        console.print("[red]Stages that failed:[/red] " + "; ".join(f"{name} ({msg[:80]})" for name, msg in failed))
        raise SystemExit(1)  # non-zero so a scheduler can notice
    console.print("[green]All stages completed.[/green]")


@cli.command("resolve-locations")
@click.option("--dry-run", is_flag=True, help="Show what would be filled in without saving.")
@click.option("--no-geocode", is_flag=True, help="Skip reverse geocoding (no network).")
@click.option("--no-bank", is_flag=True, help="Skip the DoED project-bank lists (no network).")
def resolve_locations(dry_run, no_geocode, no_bank):
    """Fill missing district/province/river/type from traceable evidence (see src/processors/location_infer.py)."""
    from .collectors.web_scraper import fetch_project_bank
    from .processors.location_infer import infer
    db_manager.init_db()
    bank = [] if no_bank else fetch_project_bank()
    with db_manager.session_scope() as s:
        rep = infer(s, geocode=not no_geocode, dry_run=dry_run, bank=bank)
    console.print(f"[green]{'Would fill' if dry_run else 'Filled'}:[/green]")
    for kind, n in sorted(rep.counts.items(), key=lambda kv: -kv[1]):
        console.print(f"  {n:4}  {kind}   e.g. {'; '.join(rep.examples[kind][:2])}")
    if not rep.counts:
        console.print("  nothing to fill")


@cli.command("validate-data")
@click.option("--details", is_flag=True, help="Print every issue, not just counts.")
def validate_data(details):
    """Audit data quality and write data/processed/validation_report.csv."""
    import csv
    from collections import Counter
    from .processors.validators import validate_database
    with db_manager.session_scope() as s:
        issues = validate_database(s)
    counts = Counter((i.severity, i.code) for i in issues)
    table = Table(title="Data quality")
    for col in ("Severity", "Check", "Count"):
        table.add_column(col)
    for (sev, code), n in sorted(counts.items()):
        table.add_row(sev, code, str(n))
    console.print(table)
    path = config.PROCESSED_DIR / "validation_report.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["severity", "code", "project_id", "message"])
        w.writerows((i.severity, i.code, i.project_id, i.message) for i in issues)
    console.print(f"Full report: {path}")
    if details:
        for i in issues:
            console.print(f"[{i.severity}] {i.project_id} {i.code}: {i.message}")




@cli.command()
@click.option("--host", default=config.FLASK_HOST, show_default=True)
@click.option("--port", default=config.FLASK_PORT, show_default=True)
def web(host, port):
    """Start the web dashboard and JSON API."""
    from .dashboard import create_app
    db_manager.init_db()
    console.print(f"[green]Dashboard:[/green] http://{host}:{port}/")
    create_app().run(host=host, port=port, debug=config.DEBUG)


@cli.command()
@click.option("--format", "kind", default="master", show_default=True,
              type=click.Choice(["master", "operational", "construction", "planned", "financial", "technical", "nepse", "all"]))
def export(kind):
    """Export Excel workbooks to exports/generated/."""
    from .exports.excel_builder import KINDS, build_workbook
    for k in (KINDS if kind == "all" else (kind,)):
        console.print(f"[green]Wrote[/green] {build_workbook(k)}")


from .cli_analyze import analyze, summary  # noqa: E402
from .cli_reports import reports  # noqa: E402

cli.add_command(summary)
cli.add_command(analyze)
cli.add_command(reports)


def main():
    cli()
