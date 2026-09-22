"""Company profiles: portfolio by stage, reported financials, and derived metrics with their basis stated.

Nothing here is a valuation or investment advice. Metrics are either reported (ShareSansar quarterly financials),
extracted from a report (unverified until confirmed), or derived: every derived number carries a `basis` string.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import config
from ..models import Company, CompanyFact, CompanyFinancial, CompanyReport, Project, ProjectStatus, ProjectUpdate
from . import financials as fin
from ..processors.geo import BELTS, ecological_belt
from .company_trends import build_trend

STAGE_ORDER = ["Operational", "Under Construction", "Licensed", "Planned"]
TAX_FREE_BELOW = 0.02  # effective tax rate under 2% of pre-tax profit reads as a tax holiday
TAX_PARTIAL_BELOW = 0.15
PLAUSIBLE_COST_PER_MW = (60e6, 700e6)  # NPR per MW; hydropower typically costs roughly NPR 150-350 m/MW


def _latest(rows: list[CompanyFinancial]) -> CompanyFinancial | None:
    """Most recent period that has balance-sheet data (a full quarterly report, not just a profit headline)."""
    full = [r for r in rows if r.loans_npr is not None or r.paid_up_capital_npr is not None]
    return max(full, key=lambda r: (r.fiscal_year, r.quarter), default=None)


def tax_status(fin_row: CompanyFinancial | None, facts: list[CompanyFact]) -> dict:
    """Tax-free or not, from reported numbers first (effective rate), with any report wording alongside."""
    mentions = [f.value_text for f in facts if f.fact_type == "tax_holiday_mention"][:3]
    out = {"label": "Unknown", "effective_rate_pct": None, "basis": "no financials loaded", "report_mentions": mentions}
    if fin_row and fin_row.tax_provision_npr is not None and fin_row.net_profit_npr is not None:
        pre_tax = fin_row.net_profit_npr + fin_row.tax_provision_npr
        if pre_tax > 0:
            rate = fin_row.tax_provision_npr / pre_tax
            out["effective_rate_pct"] = round(rate * 100, 1)
            out["label"] = ("Tax-free (holiday)" if rate < TAX_FREE_BELOW
                            else "Partly exempt" if rate < TAX_PARTIAL_BELOW else "Taxable")
            out["basis"] = (f"tax provision / pre-tax profit, FY {fin_row.fiscal_year} Q{fin_row.quarter} "
                            "(inferred from reported numbers, not a legal determination)")
        else:
            out["basis"] = f"no pre-tax profit in FY {fin_row.fiscal_year} Q{fin_row.quarter}"
    return out


def _pick_fact(facts: list[CompanyFact], fact_type: str) -> dict | None:
    """The most frequently reported value of a fact type (ties: latest fiscal year)."""
    cand = [f for f in facts if f.fact_type == fact_type and f.value_num is not None]
    if not cand:
        return None
    common = Counter(f.value_num for f in cand).most_common(1)[0][0]
    best = max((f for f in cand if f.value_num == common), key=lambda f: f.fiscal_year or "")
    return {"value": best.value_num, "fiscal_year": best.fiscal_year, "page": best.page, "snippet": best.snippet,
            "verified": best.verified, "method": best.method, "report_id": best.report_id}


def _zone_table(stages: dict[str, list[dict]], key: str, order: tuple[str, ...] | None = None) -> list[dict]:
    """Projects and MW per stage grouped by a zone field (ecological belt or province). Unknown zones are kept, not hidden."""
    zones: dict[str, dict] = {}
    for stage, rows in stages.items():
        for r in rows:
            z = zones.setdefault(r.get(key) or "Unknown", {"zone": r.get(key) or "Unknown", "projects": 0, "total_mw": 0.0,
                                                          **{st: 0.0 for st in STAGE_ORDER}})
            z["projects"] += 1
            z[stage] = round(z[stage] + (r["capacity_mw"] or 0), 1)
            z["total_mw"] = round(z["total_mw"] + (r["capacity_mw"] or 0), 1)
    out = list(zones.values())
    if order:
        return sorted(out, key=lambda z: (order.index(z["zone"]) if z["zone"] in order else len(order), -z["total_mw"]))
    return sorted(out, key=lambda z: -z["total_mw"])


def build_profile(session: Session, company_id: int) -> dict | None:
    c = session.get(Company, company_id)
    if c is None:
        return None
    projects = list(session.scalars(select(Project).where(
        or_(Project.developer_company_id == company_id, Project.owner_company_id == company_id))))
    facts = list(session.scalars(select(CompanyFact).where(CompanyFact.company_id == company_id)))
    project_ids = [p.project_id for p in projects]
    # a report by one company can state another project's cost (BPC's report gives Kabeli-A's): count verified project-linked costs
    linked_costs = list(session.scalars(select(CompanyFact).where(
        CompanyFact.project_id.in_(project_ids or [""]), CompanyFact.fact_type == "project_cost_npr", CompanyFact.verified.is_(True))))
    fin_rows = list(session.scalars(select(CompanyFinancial).where(CompanyFinancial.company_id == company_id)))
    reports = list(session.scalars(select(CompanyReport).where(CompanyReport.company_id == company_id)))
    news = list(session.scalars(select(ProjectUpdate).where(
        or_(ProjectUpdate.company_id == company_id, ProjectUpdate.project_id.in_(project_ids or [""])))))
    latest = _latest(fin_rows)
    project_cost_facts: dict[str, CompanyFact] = {}
    for f in sorted([*facts, *linked_costs], key=lambda f: (f.verified, f.fiscal_year or "")):  # verified and newest win
        if f.fact_type == "project_cost_npr" and f.project_id:
            project_cost_facts[f.project_id] = f

    stages: dict[str, list[dict]] = {s: [] for s in STAGE_ORDER}
    for p in sorted(projects, key=lambda p: -(p.capacity_mw or 0)):
        cost = p.estimated_project_cost_npr
        cost_basis = "project record" if cost else None
        if not cost and p.estimated_project_cost_usd:
            cost, cost_basis = p.estimated_project_cost_usd * config.DEFAULT_NPR_PER_USD, "USD cost x fallback FX rate"
        if not cost and p.project_id in project_cost_facts:
            f = project_cost_facts[p.project_id]
            cost = f.value_num
            cost_basis = (f"verified from a report (FY {f.fiscal_year or '?'}, p.{f.page or '?'})" if f.verified else "report (unverified)")                 + (f": {f.value_text}" if f.value_text else "")
        row = {"project_id": p.project_id, "name": p.project_name_en, "capacity_mw": p.capacity_mw,
               "district": p.district.district_name if p.district else None, "commissioning_year": p.commissioning_year,
               "expected_completion_year": p.expected_completion_year, "license_type": p.license_type,
               "license_expiry": p.license_expiry_date.isoformat() if p.license_expiry_date else None,
               "cost_npr": cost, "cost_per_mw_npr": cost / p.capacity_mw if cost and p.capacity_mw else None,
               "cost_basis": cost_basis, "role": "developer" if p.developer_company_id == company_id else "owner",
               "latitude": p.latitude, "longitude": p.longitude, "province": p.province,
               "belt": ecological_belt(p.district.district_name if p.district else None)}
        if p.status.value in stages:
            stages[p.status.value].append(row)

    def mw(stage: str) -> float:
        return round(sum(r["capacity_mw"] or 0 for r in stages[stage]), 1)
    op_mw, uc_mw, lic_mw, plan_mw = (mw(s) for s in STAGE_ORDER)

    # --- cost per MW: reported project costs first, else a book-value proxy
    costed = [r for r in stages["Operational"] + stages["Under Construction"] if r["cost_per_mw_npr"]]
    cost_per_mw, cost_note = None, None
    if costed:
        total_cost, total_mw = sum(r["cost_npr"] for r in costed), sum(r["capacity_mw"] for r in costed)
        notes = sorted({r["cost_basis"] for r in costed if r["cost_basis"]})
        cost_per_mw = {"npr": total_cost / total_mw, "basis": f"reported project costs for {len(costed)} project(s)" + (f" ({'; '.join(notes)})" if notes else "")}
    elif latest and latest.paid_up_capital_npr and (op_mw + uc_mw):
        # ShareSansar's summary balance sheet often books the plant outside "PP&E", so book asset values are unusable.
        # Capital raised (equity + loans) per MW of capacity it funds is a better, still rough, stand-in.
        capital = (latest.paid_up_capital_npr or 0) + (latest.reserves_npr or 0) + (latest.loans_npr or 0)
        proxy = capital / (op_mw + uc_mw)
        if PLAUSIBLE_COST_PER_MW[0] <= proxy <= PLAUSIBLE_COST_PER_MW[1]:
            cost_per_mw = {"npr": proxy, "basis": ("CAPITAL-EMPLOYED PROXY: (paid-up capital + reserves + loans) / (operational + "
                                                   "under-construction MW of record). Includes cash and investments; ignores "
                                                   "accumulated depreciation: a rough guide, not a project cost")}
        else:
            cost_note = (f"capital-employed proxy of NPR {proxy / 1e6:,.0f} m per MW is outside the typical "
                         f"{PLAUSIBLE_COST_PER_MW[0] / 1e6:.0f}-{PLAUSIBLE_COST_PER_MW[1] / 1e6:.0f} m range (holding company, or "
                         "capacity of record incomplete), so it is not shown")

    # Auto-extracted numbers are regex candidates on free text (one 'PPA rate' turned out to be a retail tariff), so only
    # VERIFIED values become the headline PPA rate; unverified ones are shown separately as candidates to check.
    verified = [f for f in facts if f.verified]
    ppa = _pick_fact(verified, "ppa_rate_npr_kwh")
    wet, dry = _pick_fact(verified, "ppa_wet_npr_kwh"), _pick_fact(verified, "ppa_dry_npr_kwh")
    escalation = next((f.value_text for f in verified if f.fact_type == "ppa_escalation"), None)
    ppa_rate = None
    if ppa:  # one flat rate
        ppa_rate = {"npr_per_kwh": ppa["value"], "text": f"NPR {ppa['value']:.2f} / kWh", "wet": None, "dry": None, "source": ppa}
    elif wet or dry:  # seasonal PPA: shown as wet / dry, with a blended figure for tables
        share = config.ASSUMED_DRY_SEASON_ENERGY_SHARE
        w, d = (wet or dry)["value"], (dry or wet)["value"]
        ppa_rate = {"npr_per_kwh": round(w * (1 - share) + d * share, 2), "wet": wet["value"] if wet else None, "dry": dry["value"] if dry else None,
                    "text": " / ".join(x for x in (f"wet NPR {wet['value']:.2f}" if wet else "", f"dry NPR {dry['value']:.2f}" if dry else "") if x) + " per kWh",
                    "blend_note": f"table figure blends wet and dry assuming {share:.0%} of energy in the dry season", "source": wet or dry}
    if ppa_rate:
        ppa_rate.update({"verified": True, "escalation": escalation})
    ppa_candidate = None if ppa_rate else _pick_fact(facts, "ppa_rate_npr_kwh")
    reported_revenue = latest.electricity_sales_npr if latest else None
    # --- scenario: what the pipeline could add, priced at this company's own revenue per MW when known
    rev_per_mw = reported_revenue / op_mw if reported_revenue and op_mw and latest and latest.quarter == 4 else None
    est_rev = fin.estimate_energy_gwh(1, config.ASSUMED_CAPACITY_FACTOR) * fin.blended_tariff(
        config.ASSUMED_WET_TARIFF_NPR_PER_KWH, config.ASSUMED_DRY_TARIFF_NPR_PER_KWH,
        config.ASSUMED_DRY_SEASON_ENERGY_SHARE) * 1e6  # NPR per MW-year under the config assumptions
    per_mw, per_mw_basis = (rev_per_mw, "own reported electricity sales / operational MW") if rev_per_mw else (
        est_rev, "ASSUMED tariff and capacity factor (src/config.py)")
    pipeline_mw = uc_mw + lic_mw
    equity = ((latest.paid_up_capital_npr or 0) + (latest.reserves_npr or 0)) if latest else None
    future = {
        "operational_mw": op_mw, "pipeline_mw": round(pipeline_mw, 1), "planned_mw": plan_mw,
        "capacity_if_pipeline_lands_mw": round(op_mw + pipeline_mw, 1),
        "capacity_multiple": round((op_mw + pipeline_mw) / op_mw, 2) if op_mw else None,
        "revenue_per_mw_npr": per_mw, "revenue_per_mw_basis": per_mw_basis,
        "potential_extra_revenue_npr": per_mw * pipeline_mw if pipeline_mw else 0.0,
        "capex_to_fund_npr": (cost_per_mw["npr"] * pipeline_mw) if cost_per_mw and pipeline_mw else None,
        "note": "Scenario arithmetic on stated capacities. Licensed/planned projects may never be built, and this "
                "is not a valuation.",
    }
    metrics = {
        "loans_npr": latest.loans_npr if latest else None,
        "finance_cost_npr": latest.finance_cost_npr if latest else None,
        "finance_cost_period": f"FY {latest.fiscal_year} Q{latest.quarter} (year to date)" if latest else None,
        "debt_to_equity": round(latest.loans_npr / equity, 2) if latest and latest.loans_npr is not None and equity else None,
        "finance_cost_pct_of_sales": round(latest.finance_cost_npr / latest.electricity_sales_npr * 100, 1)
        if latest and latest.finance_cost_npr is not None and latest.electricity_sales_npr else None,
        "electricity_sales_npr": reported_revenue, "net_profit_npr": latest.net_profit_npr if latest else None,
        "paid_up_capital_npr": latest.paid_up_capital_npr if latest else None,
        "eps": latest.eps if latest else None, "networth_per_share": latest.networth_per_share if latest else None,
        "roe_pct": latest.roe_pct if latest else None, "roa_pct": latest.roa_pct if latest else None,
        "financials_period": f"FY {latest.fiscal_year} Q{latest.quarter}" if latest else None,
        "cost_per_mw": cost_per_mw, "cost_per_mw_note": cost_note,
        "ppa_rate": ppa_rate,
        "ppa_candidate": ({"npr_per_kwh": ppa_candidate["value"], "source": ppa_candidate} if ppa_candidate else None),
        "tax": tax_status(latest, facts),
    }
    history = [{"fiscal_year": r.fiscal_year, "quarter": r.quarter, "net_profit_npr": r.net_profit_npr,
                "finance_cost_npr": r.finance_cost_npr, "loans_npr": r.loans_npr, "electricity_sales_npr": r.electricity_sales_npr}
               for r in sorted(fin_rows, key=lambda r: (r.fiscal_year, r.quarter), reverse=True)]
    return {
        "company": {"company_id": c.company_id, "name": c.company_name, "symbol": c.stock_symbol, "listed": c.nepse_listed,
                    "website": c.website, "website_source": c.website_source, "email": c.email, "address": c.address,
                    "listed_shares": c.listed_shares},
        "geo": {"by_belt": _zone_table(stages, "belt", BELTS), "by_province": _zone_table(stages, "province"),
                "mapped": sum(1 for rows in stages.values() for r in rows if r["latitude"] is not None and r["longitude"] is not None),
                "unmapped": sum(1 for rows in stages.values() for r in rows if r["latitude"] is None or r["longitude"] is None),
                "note": ("Ecological belt is the government's district-level Mountain / Hill / Terai classification; a district "
                         "sits wholly in one belt, so borderline districts are approximate. Map positions are the midpoint of the "
                         "licence area reported by DoED.")},
        "portfolio": {"stages": stages, "mw": {"Operational": op_mw, "Under Construction": uc_mw, "Licensed": lic_mw, "Planned": plan_mw},
                      "projects": len(projects)},
        "metrics": metrics, "future": future, "history": history, "trend": build_trend(session, company_id),
        "news": [{"date": u.update_date.isoformat(), "title": u.title, "source": u.source_name, "url": u.source_url,
                  "project_id": u.project_id, "project": u.project.project_name_en if u.project else None}
                 for u in sorted(news, key=lambda u: u.update_date, reverse=True)],
        "reports": [{"report_id": r.report_id, "kind": r.kind, "fiscal_year": r.fiscal_year, "quarter": r.quarter,
                     "title": r.title, "status": r.status, "url": r.source_url, "local_path": r.local_path}
                    for r in sorted(reports, key=lambda r: (r.kind, r.fiscal_year or ""), reverse=True)],
        "facts": [{"fact_type": f.fact_type, "value_num": f.value_num, "value_text": f.value_text, "unit": f.unit,
                   "fiscal_year": f.fiscal_year, "page": f.page, "snippet": f.snippet, "verified": f.verified,
                   "method": f.method, "project_id": f.project_id} for f in facts],
    }


def list_profiles(session: Session, listed_only: bool = True) -> list[dict]:
    """One summary row per company for the Companies table."""
    stmt = select(Company).where(Company.nepse_listed.is_(True)) if listed_only else select(Company)
    rows = []
    for c in session.scalars(stmt):
        p = build_profile(session, c.company_id)
        if not listed_only and not p["portfolio"]["projects"]:
            continue
        m, mw = p["metrics"], p["portfolio"]["mw"]
        rows.append({
            "company_id": c.company_id, "name": c.company_name, "symbol": c.stock_symbol, "projects": p["portfolio"]["projects"],
            "operational_mw": mw["Operational"], "construction_mw": mw["Under Construction"], "licensed_mw": mw["Licensed"],
            "planned_mw": mw["Planned"], "cost_per_mw_npr": (m["cost_per_mw"] or {}).get("npr"),
            "cost_per_mw_basis": (m["cost_per_mw"] or {}).get("basis") or m["cost_per_mw_note"],
            "ppa_rate": (m["ppa_rate"] or {}).get("npr_per_kwh"), "ppa_text": (m["ppa_rate"] or {}).get("text"), "tax": m["tax"]["label"],
            "effective_tax_rate_pct": m["tax"]["effective_rate_pct"], "loans_npr": m["loans_npr"],
            "finance_cost_npr": m["finance_cost_npr"], "debt_to_equity": m["debt_to_equity"],
            "financials_period": m["financials_period"], "reports": len(p["reports"]),
            "profit_yoy_pct": (p["trend"]["latest"] or {}).get("yoy_pct"), "latest_period": (p["trend"]["latest"] or {}).get("period"),
        })
    return sorted(rows, key=lambda r: (-r["operational_mw"], -r["construction_mw"], -(r["licensed_mw"] + r["planned_mw"])))
