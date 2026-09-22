"""Developer rankings and NEPSE-listed company exposure."""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Company, Project, Shareholder, project_shareholder

PIPELINE = ("Under Construction", "Licensed")


def developer_ranking(df: pd.DataFrame, top: int = 20) -> pd.DataFrame:
    """Developers ranked by operational MW, with pipeline MW alongside."""
    d = df.assign(developer=df["developer"].fillna("Unknown"))
    op = d[d["status"] == "Operational"].groupby("developer").agg(
        op_projects=("project_id", "count"), op_mw=("capacity_mw", "sum"))
    pipe = d[d["status"].isin(PIPELINE)].groupby("developer").agg(
        pipe_projects=("project_id", "count"), pipe_mw=("capacity_mw", "sum"))
    out = op.join(pipe, how="outer").fillna(0).reset_index()
    out = out.sort_values(["op_mw", "pipe_mw"], ascending=False).head(top)
    for col in ("op_projects", "pipe_projects"):
        out[col] = out[col].astype(int)
    return out.round(1)


def nepse_exposure(df: pd.DataFrame, session: Session) -> pd.DataFrame:
    """Per NEPSE-listed company: capacity it holds directly (as owner/developer of record).

    Limitation: holdings through subsidiaries or minority stakes are not visible in DoED data
    (see the `shareholders` table / `import-csv --kind shareholders` for adding them).
    """
    listed = df[df["symbol"].notna()]
    if listed.empty:
        return pd.DataFrame()
    rows = []
    for sym, g in listed.groupby("symbol"):
        rows.append({
            "symbol": sym,
            "company": g["owner"].fillna(g["developer"]).iloc[0],
            "projects": len(g),
            "op_mw": g.loc[g["status"] == "Operational", "capacity_mw"].sum(),
            "pipe_mw": g.loc[g["status"].isin(PIPELINE), "capacity_mw"].sum(),
            "planned_mw": g.loc[g["status"] == "Planned", "capacity_mw"].sum(),
        })
    out = pd.DataFrame(rows)
    comp = pd.DataFrame(session.execute(
        select(Company.stock_symbol, Company.listed_shares, Company.paidup_value)
        .where(Company.nepse_listed.is_(True))).all(), columns=["symbol", "listed_shares", "paidup_value"])
    out = out.merge(comp, on="symbol", how="left")
    out["paidup_npr_m"] = (out["listed_shares"] * out["paidup_value"] / 1e6).round(0)
    capital_bn = (out["paidup_npr_m"] / 1000).where(out["paidup_npr_m"] > 0)  # zero/unknown capital -> NaN, not inf
    out["op_mw_per_bn_paidup"] = (out["op_mw"] / capital_bn).round(1)
    return (out.drop(columns=["listed_shares", "paidup_value"])
               .sort_values("op_mw", ascending=False).round(1).reset_index(drop=True))


def listed_without_projects(session: Session, df: pd.DataFrame) -> pd.DataFrame:
    """Listed hydropower companies with no matched project: candidates for name-matching review."""
    have = set(df["symbol"].dropna())
    rows = session.execute(select(Company.stock_symbol, Company.company_name)
                           .where(Company.nepse_listed.is_(True))).all()
    return pd.DataFrame([r for r in rows if r[0] not in have], columns=["symbol", "company"])


def company_projects(df: pd.DataFrame, query: str) -> pd.DataFrame:
    """Projects of a company matched by NEPSE symbol or a name fragment."""
    q = query.strip().lower()
    mask = (df["symbol"].fillna("").str.lower() == q) | df["developer"].fillna("").str.lower().str.contains(q, regex=False) \
        | df["owner"].fillna("").str.lower().str.contains(q, regex=False)
    return df[mask].sort_values("capacity_mw", ascending=False)[
        ["project_id", "name", "capacity_mw", "status", "district", "commissioning_year"]]


def project_shareholders(session: Session, project_id: str) -> pd.DataFrame:
    rows = session.execute(
        select(Shareholder.shareholder_name, Shareholder.shareholder_type, Shareholder.stock_symbol,
               project_shareholder.c.stake_percentage)
        .join(project_shareholder, project_shareholder.c.shareholder_id == Shareholder.shareholder_id)
        .where(project_shareholder.c.project_id == project_id)).all()
    return pd.DataFrame([(n, t.value if t else None, s, p) for n, t, s, p in rows],
                        columns=["shareholder", "type", "symbol", "stake_pct"])


def suggest_matches(session: Session, df: pd.DataFrame, cutoff: float = 0.7) -> pd.DataFrame:
    """Review aid: for listed companies with no project, propose similarly named unlisted promoters.

    These are SUGGESTIONS only. Confirm real ones by adding `symbol,company_name` to data/company_aliases.csv.
    """
    import difflib

    from ..processors.cleaners import company_key
    unmatched = listed_without_projects(session, df)
    promoters = df[df["symbol"].isna() & df["developer"].notna()].groupby("developer").agg(
        projects=("project_id", "count"), mw=("capacity_mw", "sum")).reset_index()
    keyed = [(company_key(r.developer), r) for r in promoters.itertuples()]
    rows = []
    for r in unmatched.itertuples():
        k = company_key(r.company)
        scored = sorted(((difflib.SequenceMatcher(None, k, pk).ratio(), p) for pk, p in keyed), key=lambda x: -x[0])[:2]
        for ratio, p in scored:
            if ratio >= cutoff:
                rows.append({"symbol": r.symbol, "listed_name": r.company, "candidate_promoter": p.developer,
                             "similarity": round(ratio, 2), "projects": p.projects, "mw": round(p.mw, 1)})
    return pd.DataFrame(rows)
