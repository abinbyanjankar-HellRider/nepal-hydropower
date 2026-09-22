"""Capacity, status, timeline and licence analytics."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

GROUPINGS = {"status": "status", "province": "province", "district": "district", "river": "river",
             "type": "project_type", "developer": "developer"}
SIZE_BINS = [0, 1, 10, 50, 100, 500, float("inf")]
SIZE_LABELS = ["<1 MW", "1-10 MW", "10-50 MW", "50-100 MW", "100-500 MW", "500+ MW"]


def capacity_by(df: pd.DataFrame, by: str, status: str | None = None) -> pd.DataFrame:
    """Projects and total MW grouped by status/province/district/river/type/developer/size."""
    d = df if status is None else df[df["status"] == status]
    if by == "size":
        d = d.assign(group=pd.cut(d["capacity_mw"], SIZE_BINS, labels=SIZE_LABELS, right=False))
    else:
        d = d.assign(group=d[GROUPINGS[by]].fillna("Unknown"))
    out = (d.groupby("group", observed=True)
             .agg(projects=("project_id", "count"), total_mw=("capacity_mw", "sum"))
             .sort_values("total_mw", ascending=False).reset_index())
    total = out["total_mw"].sum()
    out["share_pct"] = (out["total_mw"] / total * 100).round(1) if total else 0.0
    out["total_mw"] = out["total_mw"].round(1)
    return out


def top_projects(df: pd.DataFrame, n: int = 15, status: str | None = None) -> pd.DataFrame:
    d = df if status is None else df[df["status"] == status]
    return d.sort_values("capacity_mw", ascending=False).head(n)[
        ["project_id", "name", "capacity_mw", "status", "province", "developer"]]


def commissioning_timeline(df: pd.DataFrame) -> pd.DataFrame:
    """MW added per year by operational projects, with a running total."""
    op = df[(df["status"] == "Operational") & df["commissioning_year"].notna()]
    out = (op.groupby(op["commissioning_year"].astype(int))
             .agg(projects=("project_id", "count"), mw_added=("capacity_mw", "sum")).reset_index()
             .rename(columns={"commissioning_year": "year"}))
    out["cumulative_mw"] = out["mw_added"].cumsum().round(1)
    out["mw_added"] = out["mw_added"].round(1)
    return out


def pipeline_by_year(df: pd.DataFrame) -> pd.DataFrame:
    """Under-construction capacity by expected completion year (year is only known for some)."""
    uc = df[df["status"] == "Under Construction"].copy()
    uc["expected_year"] = uc["expected_completion_year"].fillna(0).astype(int).replace(0, "Unknown")
    out = (uc.groupby("expected_year").agg(projects=("project_id", "count"), total_mw=("capacity_mw", "sum"))
             .reset_index())
    out["total_mw"] = out["total_mw"].round(1)
    return out.sort_values("expected_year", key=lambda s: s.astype(str))


def expiring_licences(df: pd.DataFrame, within_days: int = 365, today: date | None = None) -> pd.DataFrame:
    """Non-operational projects whose licence has expired or expires soon."""
    today = today or date.today()
    d = df[(df["status"] != "Operational") & df["license_expiry_date"].notna()].copy()
    d["days_left"] = d["license_expiry_date"].map(lambda x: (x - today).days)
    d = d[d["days_left"] <= within_days]
    return d.sort_values("days_left")[["project_id", "name", "capacity_mw", "status", "license_type",
                                       "license_expiry_date", "days_left", "developer"]]


def summary(df: pd.DataFrame) -> dict:
    op = df[df["status"] == "Operational"]
    return {
        "projects": len(df),
        "operational_projects": len(op),
        "operational_mw": round(op["capacity_mw"].sum(), 1),
        "under_construction_mw": round(df[df["status"] == "Under Construction"]["capacity_mw"].sum(), 1),
        "licensed_mw": round(df[df["status"] == "Licensed"]["capacity_mw"].sum(), 1),
        "planned_mw": round(df[df["status"] == "Planned"]["capacity_mw"].sum(), 1),
        "largest_operational": op.sort_values("capacity_mw", ascending=False)["name"].head(1).tolist(),
    }


def forecast_capacity(df: pd.DataFrame, delay_years: int = 0, start_year: int | None = None,
                      horizon: int = 6, completion_rate: float = 1.0) -> pd.DataFrame:
    """Scenario: operational MW today plus under-construction MW landing in each project's expected year.

    Deliberately simple and transparent, not a prediction:
      * only Under Construction projects with a stated expected completion year are included;
      * `delay_years` shifts every completion (Nepal projects commonly slip: try 1-3);
      * `completion_rate` scales the MW that actually arrives (e.g. 0.8 if 20% stall).
    Projects with a past expected year land in the first forecast year.
    """
    start_year = start_year or date.today().year
    op_mw = df.loc[df["status"] == "Operational", "capacity_mw"].sum()
    uc = df[(df["status"] == "Under Construction") & df["expected_completion_year"].notna()]
    arrival = (uc["expected_completion_year"].astype(int) + delay_years).clip(lower=start_year)
    by_year = uc["capacity_mw"].groupby(arrival).sum() * completion_rate
    rows, cumulative = [], float(op_mw)
    for year in range(start_year, start_year + horizon + 1):
        added = float(by_year.get(year, 0.0))
        cumulative += added
        rows.append({"year": year, "mw_added": round(added, 1), "projects": int((arrival == year).sum()),
                     "cumulative_mw": round(cumulative, 1)})
    return pd.DataFrame(rows)
