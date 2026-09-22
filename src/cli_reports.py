"""`reports` commands: company info, annual/quarterly report discovery + download, financials, fact extraction."""
from __future__ import annotations

import click
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table
from sqlalchemy import func, select

from . import config
from .database import db_manager
from .models import Company, CompanyFact, CompanyFinancial, CompanyReport

console = Console()


def _companies(symbol: str | None, limit: int | None):
    with db_manager.session_scope() as s:
        stmt = select(Company).where(Company.nepse_listed.is_(True)).order_by(Company.stock_symbol)
        if symbol:
            stmt = stmt.where(Company.stock_symbol == symbol.upper())
        rows = list(s.scalars(stmt))
    return rows[:limit] if limit else rows


def _each(companies, label, fn):
    """Run fn(session, company) per company in its own transaction; keep going on errors; return failures."""
    failures = []
    with Progress(TextColumn("[bold]{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"),
                  TextColumn("{task.fields[sym]}"), TimeElapsedColumn(), console=console) as bar:
        task = bar.add_task(label, total=len(companies), sym="")
        for c in companies:
            bar.update(task, sym=c.stock_symbol)
            try:
                with db_manager.session_scope() as s:
                    fn(s, s.get(Company, c.company_id))
            except Exception as exc:  # network / layout problems for one company must not stop the run
                failures.append((c.stock_symbol, str(exc)[:120]))
            bar.advance(task)
    return failures


def _report_failures(failures):
    for sym, msg in failures[:10]:
        console.print(f"[red]{sym}: {msg}[/red]")
    if len(failures) > 10:
        console.print(f"[red]... and {len(failures) - 10} more failures[/red]")


@click.group()
def reports():
    """Company reports: find/download annual and quarterly reports, load financials, extract facts."""


common = [click.option("--symbol", help="Only this NEPSE symbol."), click.option("--limit", type=int, help="Only the first N companies.")]


def with_common(f):
    for opt in reversed(common):
        f = opt(f)
    return f


@reports.command("info")
@with_common
def info_cmd(symbol, limit):
    """Stage 1: company details (website/email) from ShareSansar."""
    from .collectors import sharesansar as ss
    db_manager.init_db()
    client = ss.ShareSansarClient()

    def run(s, c):
        ss.store_info(c, client.info(c.stock_symbol))
    fails = _each(_companies(symbol, limit), "company info", run)
    with db_manager.session_scope() as s:
        n = s.scalar(select(func.count()).select_from(Company).where(Company.nepse_listed.is_(True), Company.website.is_not(None)))
    console.print(f"[green]Done.[/green] {n} listed companies have a website on record; {len(fails)} failures.")
    _report_failures(fails)


@reports.command("discover")
@with_common
def discover_cmd(symbol, limit):
    """Stage 2: crawl company websites for annual/quarterly report PDF links."""
    from .collectors import company_reports as cr
    stats = {"sites": 0, "links": 0, "new": 0, "unreachable": 0, "no_site": 0}

    def run(s, c):
        r = cr.discover(s, c)
        if r["note"] == "no website known":
            stats["no_site"] += 1
        elif r["note"]:
            stats["unreachable"] += 1
        else:
            stats["sites"] += 1
        stats["links"] += r["pdf_links"]
        stats["new"] += r["new"]
    fails = _each(_companies(symbol, limit), "discover reports", run)
    console.print(f"[green]Discovery:[/green] {stats}")
    _report_failures(fails)


@reports.command("fetch")
@click.option("--kind", type=click.Choice(["annual", "quarterly", "all"]), default="all", show_default=True)
@with_common
def fetch_cmd(kind, symbol, limit):
    """Stage 3/4: download discovered PDFs. `all` fetches every ANNUAL report first, then every QUARTERLY report."""
    from .collectors import company_reports as cr
    for k in (("annual", "quarterly") if kind == "all" else (kind,)):
        counts = {"downloaded": 0, "failed": 0, "already": 0}

        def run(s, c, k=k, counts=counts):
            for rep in s.scalars(select(CompanyReport).where(CompanyReport.company_id == c.company_id, CompanyReport.kind == k)):
                if rep.status == "downloaded" or rep.status == "extracted":
                    counts["already"] += 1
                    continue
                counts[cr.download(s, rep, c.stock_symbol)] += 1
                s.commit()
        _report_failures(_each(_companies(symbol, limit), f"download {k} reports", run))
        console.print(f"[green]{k}:[/green] {counts}")


@reports.command("financials")
@with_common
def financials_cmd(symbol, limit):
    """Structured quarterly financials (loans, finance cost, profit, ratios) from ShareSansar."""
    from .collectors import sharesansar as ss
    db_manager.init_db()
    client = ss.ShareSansarClient()
    tot = {"quarterly": 0, "history": 0}

    def run(s, c):
        info = client.info(c.stock_symbol)
        ss.store_info(c, info)
        counts = ss.store_financials(s, c, client.financials(c.stock_symbol, info))
        for k, v in counts.items():
            tot[k] += v
    fails = _each(_companies(symbol, limit), "quarterly financials", run)
    console.print(f"[green]Financials:[/green] {tot['quarterly']} latest-quarter reports, {tot['history']} quarterly profit records; {len(fails)} failures")
    _report_failures(fails)


@reports.command("extract")
@with_common
def extract_cmd(symbol, limit):
    """Extract fact candidates (project cost, PPA rate, tax holiday) from downloaded PDFs."""
    from .processors import report_extract as rx
    total = {"reports": 0, "facts": 0, "unreadable": 0}

    def run(s, c):
        for rep in s.scalars(select(CompanyReport).where(CompanyReport.company_id == c.company_id, CompanyReport.status == "downloaded")):
            try:
                facts = rx.dedupe(rx.extract_from_pdf(config.BASE_DIR / rep.local_path))
            except Exception as exc:
                rep.status, rep.note = "failed", f"extract: {exc}"[:290]
                total["unreadable"] += 1
                continue
            for f in facts:
                s.add(CompanyFact(company_id=c.company_id, report_id=rep.report_id, fiscal_year=rep.fiscal_year, method="auto", **f))
            rep.status = "extracted"
            total["reports"] += 1
            total["facts"] += len(facts)
    _report_failures(_each(_companies(symbol, limit), "extract facts", run))
    console.print(f"[green]Extraction:[/green] {total}. These are unverified candidates; each keeps its page and snippet.")


@reports.command("guess-websites")
@click.option("--search", "use_search", is_flag=True, help="Also web-search companies the domain guesses missed.")
@with_common
def guess_websites_cmd(use_search, symbol, limit):
    """Find websites for listed companies with none on record: guess likely domains, optionally web-search; every
    candidate must be a real page that mentions the company (parked/unrelated sites are rejected)."""
    from concurrent.futures import ThreadPoolExecutor

    from .collectors import company_reports as cr
    todo = [c for c in _companies(symbol, None) if not c.website][: limit or None]
    console.print(f"Guessing websites for {len(todo)} companies (this takes a few minutes)...")
    with ThreadPoolExecutor(max_workers=8) as pool:
        found = list(pool.map(lambda c: cr.guess_website(c.company_name, c.stock_symbol), todo))
    hits = 0
    if use_search:
        rest = [c for c, site in zip(todo, found) if not site]
        console.print(f"Web-searching {len(rest)} companies the domain guesses missed (about 2 s each)...")
        for c in rest:
            found[todo.index(c)] = cr.find_website_by_search(c.company_name)
    with db_manager.session_scope() as s:
        for c, site in zip(todo, found):
            if site:
                row = s.get(Company, c.company_id)
                row.website, row.website_source = site, "guessed+verified"
                hits += 1
                console.print(f"  {c.stock_symbol:7} {site}")
    console.print(f"[green]{hits} of {len(todo)} websites found[/green]. Next: reports discover / fetch / extract.")


@reports.command("verify-fact")
@click.argument("fact_id", type=int)
@click.option("--project", "project_id", help="Project this figure belongs to (e.g. HP_001); only then does it feed cost per MW.")
@click.option("--note", required=True, help="What you confirmed, e.g. 'revised cost, excluding interest during construction'.")
def verify_fact_cmd(fact_id, project_id, note):
    """Mark an auto-extracted fact as verified after you read its source sentence, optionally linking it to a project."""
    from .models import Project
    with db_manager.session_scope() as s:
        f = s.get(CompanyFact, fact_id)
        if f is None:
            raise click.ClickException(f"No fact #{fact_id}")
        if project_id and s.get(Project, project_id) is None:
            raise click.ClickException(f"Unknown project {project_id}")
        f.verified, f.method, f.project_id, f.value_text = True, "manual (reviewed)", project_id or f.project_id, note[:200]
    console.print(f"[green]Fact #{fact_id} verified[/green]" + (f" and linked to {project_id}" if project_id else ""))


@reports.command("reject-fact")
@click.argument("fact_id", type=int)
@click.option("--reason", required=True, help="Why it is not what it looked like (kept for the audit trail).")
def reject_fact_cmd(fact_id, reason):
    """Mark a fact as wrongly extracted. It is kept, renamed 'rejected_...', and ignored by profiles."""
    with db_manager.session_scope() as s:
        f = s.get(CompanyFact, fact_id)
        if f is None:
            raise click.ClickException(f"No fact #{fact_id}")
        if not f.fact_type.startswith("rejected_"):
            f.fact_type = "rejected_" + f.fact_type
        f.verified, f.value_text = False, reason[:200]
    console.print(f"[green]Fact #{fact_id} rejected[/green]: {reason}")


@reports.command("set-website")
@click.argument("symbol")
@click.argument("url")
def set_website_cmd(symbol, url):
    """Tell the crawler where a company's website is (most listed companies have none on record), then run
    `reports discover --symbol SYMBOL` and `reports fetch --symbol SYMBOL`."""
    if not url.startswith("http"):
        url = "https://" + url
    with db_manager.session_scope() as s:
        c = s.scalar(select(Company).where(Company.stock_symbol == symbol.upper()))
        if c is None:
            raise click.ClickException(f"Unknown symbol {symbol}")
        c.website, c.website_source = url, "manual"
    console.print(f"[green]{symbol.upper()} website set to {url}[/green]")


@reports.command("add-url")
@click.argument("symbol")
@click.argument("url")
@click.option("--kind", type=click.Choice(["annual", "quarterly"]), default="annual", show_default=True)
@click.option("--fy", help="Fiscal year in BS, e.g. 2080/81")
@click.option("--quarter", type=click.IntRange(1, 4))
def add_url_cmd(symbol, url, kind, fy, quarter):
    """Register a specific report PDF URL you know of; `reports fetch` will download it."""
    with db_manager.session_scope() as s:
        c = s.scalar(select(Company).where(Company.stock_symbol == symbol.upper()))
        if c is None:
            raise click.ClickException(f"Unknown symbol {symbol}")
        if s.scalar(select(CompanyReport).where(CompanyReport.company_id == c.company_id, CompanyReport.source_url == url)):
            raise click.ClickException("That URL is already registered")
        s.add(CompanyReport(company_id=c.company_id, kind=kind, fiscal_year=fy, quarter=quarter, title="manually added",
                            source_site="manual", source_url=url, status="found"))
    console.print(f"[green]Registered.[/green] Now run: python main.py reports fetch --kind {kind} --symbol {symbol.upper()}")


@reports.command("run")
@with_common
@click.pass_context
def run_cmd(ctx, symbol, limit):
    """Everything, in order: company info -> ANNUAL reports -> QUARTERLY reports + financials -> fact extraction."""
    ctx.invoke(info_cmd, symbol=symbol, limit=limit)
    ctx.invoke(discover_cmd, symbol=symbol, limit=limit)
    ctx.invoke(fetch_cmd, kind="annual", symbol=symbol, limit=limit)
    ctx.invoke(fetch_cmd, kind="quarterly", symbol=symbol, limit=limit)
    ctx.invoke(financials_cmd, symbol=symbol, limit=limit)
    ctx.invoke(extract_cmd, symbol=symbol, limit=limit)
    ctx.invoke(status_cmd)


@reports.command("status")
def status_cmd():
    """Coverage summary: what was found, downloaded and extracted."""
    with db_manager.session_scope() as s:
        listed = s.scalar(select(func.count()).select_from(Company).where(Company.nepse_listed.is_(True)))
        with_site = s.scalar(select(func.count()).select_from(Company).where(Company.nepse_listed.is_(True), Company.website.is_not(None)))
        t = Table(title=f"Report coverage ({listed} listed hydropower companies, {with_site} with a website on record)")
        for col in ("Kind", "Status", "Reports", "Companies"):
            t.add_column(col)
        for kind, status, n, cos in s.execute(select(CompanyReport.kind, CompanyReport.status, func.count(),
                                                     func.count(func.distinct(CompanyReport.company_id))).group_by(CompanyReport.kind, CompanyReport.status)):
            t.add_row(kind, status, str(n), str(cos))
        console.print(t)
        fin_cos = s.scalar(select(func.count(func.distinct(CompanyFinancial.company_id))))
        facts = dict(s.execute(select(CompanyFact.fact_type, func.count()).group_by(CompanyFact.fact_type)).all())
        console.print(f"Structured quarterly financials: {fin_cos} companies. Extracted fact candidates: {facts or 'none'}")
