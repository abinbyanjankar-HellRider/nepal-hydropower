"""`summary` and `analyze ...` commands."""
from __future__ import annotations

import click
import pandas as pd
from rich.console import Console
from rich.table import Table

from .analytics import financials as fin
from .analytics import load_projects_df
from .analytics import ownership as own
from .analytics import technical as tech
from .database import db_manager

console = Console()
STATUSES = ["Operational", "Under Construction", "Licensed", "Planned"]


def _fmt(col: str, value) -> str:
    if pd.isna(value):
        return "-"
    if "year" in col:
        return str(int(value)) if isinstance(value, (int, float)) and value == int(value) else str(value)
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int) or (isinstance(value, float) and col in INT_COLUMNS):
        return f"{int(value):,}"
    if isinstance(value, float):
        return f"{value:,.1f}"
    return str(value)


INT_COLUMNS = {"projects", "days_left", "op_projects", "pipe_projects"}


def show(df: pd.DataFrame, title: str, max_rows: int | None = None) -> None:
    if df is None or df.empty:
        console.print(f"[yellow]{title}: no data[/yellow]")
        return
    table = Table(title=title)
    for col in df.columns:
        table.add_column(str(col), justify="right" if pd.api.types.is_numeric_dtype(df[col]) else "left")
    d = (df.head(max_rows) if max_rows else df).astype(object)
    for row in d.itertuples(index=False):
        table.add_row(*[_fmt(col, v) for col, v in zip(d.columns, row)])
    console.print(table)


def _df():
    with db_manager.session_scope() as s:
        df = load_projects_df(s)
    if df.empty:
        raise click.ClickException("No projects in the database. Run `python main.py sync` first.")
    return df


@click.command()
def summary():
    """Headline numbers for the whole database."""
    df = _df()
    s = tech.summary(df)
    table = Table(title="Nepal hydropower: overview")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Projects tracked", f"{s['projects']:,}")
    table.add_row("Operational", f"{s['operational_projects']:,} projects / {s['operational_mw']:,.0f} MW")
    table.add_row("Under construction", f"{s['under_construction_mw']:,.0f} MW")
    table.add_row("Licensed (generation licence, not yet building)", f"{s['licensed_mw']:,.0f} MW")
    table.add_row("Planned (survey licence / proposals)", f"{s['planned_mw']:,.0f} MW")
    table.add_row("Largest operational", ", ".join(s["largest_operational"]))
    console.print(table)
    unverified = (df["data_reliability"] == "seed-unverified").sum()
    if unverified:
        console.print(f"[yellow]{unverified} project(s) are unverified seed rows.[/yellow]")


@click.group()
def analyze():
    """Analytics on the project database."""


@analyze.command()
@click.option("--by", type=click.Choice(["status", "province", "district", "river", "type", "developer", "size"]),
              default="status", show_default=True)
@click.option("--status", type=click.Choice(STATUSES), help="Restrict to one status.")
@click.option("--top", default=20, show_default=True)
def capacity(by, status, top):
    """Projects and MW grouped by a dimension."""
    show(tech.capacity_by(_df(), by, status), f"Capacity by {by}" + (f" ({status})" if status else ""), top)


@analyze.command()
@click.option("--status", type=click.Choice(STATUSES))
@click.option("--top", default=15, show_default=True)
def top(status, top):
    """Largest projects."""
    show(tech.top_projects(_df(), top, status), "Largest projects" + (f" ({status})" if status else ""))


@analyze.command()
def timeline():
    """MW added per commissioning year (operational projects)."""
    show(tech.commissioning_timeline(_df()), "Commissioning timeline")


@analyze.command()
def pipeline():
    """Under-construction capacity by expected completion year."""
    show(tech.pipeline_by_year(_df()), "Construction pipeline")


@analyze.command()
@click.option("--delay-years", default=0, show_default=True, help="Assume every completion slips by N years.")
@click.option("--completion-rate", default=1.0, show_default=True, help="Share of pipeline MW that actually arrives (0-1).")
@click.option("--horizon", default=6, show_default=True)
def forecast(delay_years, completion_rate, horizon):
    """Scenario: operational capacity if the construction pipeline lands (with optional slippage)."""
    df = _df()
    known = df[(df["status"] == "Under Construction")]
    console.print(f"[yellow]Scenario, not a prediction.[/yellow] {known['expected_completion_year'].notna().sum()} of "
                  f"{len(known)} under-construction projects state an expected year; the rest are excluded.")
    show(tech.forecast_capacity(df, delay_years, horizon=horizon, completion_rate=completion_rate),
         f"Operational capacity scenario (delay {delay_years}y, {completion_rate:.0%} completion)")


@analyze.command()
@click.option("--days", default=365, show_default=True, help="Show licences expiring within N days (or already expired).")
@click.option("--top", default=25, show_default=True)
def licences(days, top):
    """Non-operational projects with expired / soon-to-expire licences."""
    show(tech.expiring_licences(_df(), days), "Licence expiry watch", top)


@analyze.command()
@click.option("--top", default=20, show_default=True)
def developers(top):
    """Developers ranked by operational capacity."""
    show(own.developer_ranking(_df(), top), "Developer ranking")


@analyze.command()
@click.option("--suggest", is_flag=True, help="Suggest promoter names for listed companies with no matched project.")
def nepse(suggest):
    """NEPSE-listed hydropower companies and the capacity they hold directly."""
    with db_manager.session_scope() as s:
        df = load_projects_df(s)
        exposure = own.nepse_exposure(df, s)
        missing = own.listed_without_projects(s, df)
        suggestions = own.suggest_matches(s, df) if suggest else None
    if suggest:
        show(suggestions, "Suggested links (review; confirm in data/company_aliases.csv as `symbol,company_name`)", 60)
        return
    show(exposure, "NEPSE-listed exposure (direct holdings of record; excludes subsidiaries/minority stakes)", 40)
    if not missing.empty:
        console.print(f"[dim]{len(missing)} listed hydropower companies have no matched project "
                      f"(name mismatch or projects not yet licensed): "
                      f"{', '.join(missing['symbol'].head(12))}{'...' if len(missing) > 12 else ''}[/dim]")


@analyze.command("company")
@click.argument("query")
def company_cmd(query):
    """Projects of a company, by NEPSE symbol or name fragment (e.g. `analyze company CHCL`)."""
    show(own.company_projects(_df(), query), f"Projects matching '{query}'", 50)


@analyze.command()
@click.option("--capacity-factor", type=float, help="Override assumed capacity factor (0-1).")
@click.option("--wet-tariff", type=float, help="NPR/kWh wet season.")
@click.option("--dry-tariff", type=float, help="NPR/kWh dry season.")
@click.option("--dry-share", type=float, help="Share of annual energy in dry season (0-1).")
@click.option("--top", default=20, show_default=True)
def revenue(capacity_factor, wet_tariff, dry_tariff, dry_share, top):
    """ESTIMATED annual revenue for operational projects (scenario tool, not reported revenue)."""
    from .analytics import income
    with db_manager.session_scope() as s:
        rates = income.load_ppa_rates(s)
    est = fin.estimate_revenue_df(_df(), capacity_factor, wet_tariff, dry_tariff, dry_share, rates=rates)
    verified = int(est["tariff_basis"].isin(["verified project", "verified company"]).sum())
    console.print(f"[yellow]Estimates use assumed capacity factors (see src/config.py); {verified} of {len(est)} plants are "
                  "priced at a verified PPA rate and the rest at the assumed tariff. They are not reported figures.[/yellow]")
    console.print(f"Total estimated: {est['est_energy_gwh'].sum():,.0f} GWh, "
                  f"NPR {est['est_revenue_npr_m'].sum() / 1000:,.1f} billion/year across {len(est)} plants")
    show(est.sort_values("est_revenue_npr_m", ascending=False), "Estimated annual revenue (NPR million)", top)


@analyze.command()
@click.argument("project_id")
def project(project_id):
    """Everything known about one project."""
    from .models import Project
    with db_manager.session_scope() as s:
        p = s.get(Project, project_id)
        if p is None:
            raise click.ClickException(f"Unknown project {project_id}")
        table = Table(title=f"{p.project_id}: {p.project_name_en}", show_header=False)
        table.add_column("Field")
        table.add_column("Value")
        skip = {"created_at", "updated_at"}
        for col in p.__table__.columns:
            v = getattr(p, col.name)
            if v is not None and col.name not in skip and not col.name.endswith("_id") or col.name == "project_id":
                table.add_row(col.name, str(getattr(v, "value", v)))
        table.add_row("district", p.district.district_name if p.district else "-")
        table.add_row("developer", p.developer.company_name if p.developer else "-")
        if p.developer and p.developer.stock_symbol:
            table.add_row("NEPSE symbol", p.developer.stock_symbol)
        console.print(table)
        show(fin.financial_history(s, project_id), "Reported financials")
        show(own.project_shareholders(s, project_id), "Shareholders")
        for u in sorted(p.updates, key=lambda u: u.update_date, reverse=True)[:5]:
            console.print(f"  {u.update_date} [{u.source_name}] {u.title}")
