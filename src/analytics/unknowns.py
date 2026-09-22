"""What 'Unknown' means in the analytics: which projects lack each attribute, how much capacity that is, and why."""
from __future__ import annotations

import pandas as pd

DIMENSIONS = {
    "province": ("Province", lambda d: d["province"] == "Unknown"),
    "district": ("District", lambda d: d["district"] == "Unknown"),
    "river": ("River", lambda d: d["river"].isna()),
    "developer": ("Developer / promoter", lambda d: d["developer"].isna()),
    "type": ("Project type", lambda d: d["project_type"].isna()),
}
SOURCE_LABEL = {
    "wikipedia": "Wikipedia-only entry (its tables give no such detail)",
    "doed": "DoED register row (field blank in the register)",
    "seed-unverified": "Hand-entered seed row",
    "csv-import": "CSV import", "api": "Added through the API",
}
WHY = {
    "province": "Wikipedia's upcoming-project tables list name, MW and owner but no location; DoED rows sometimes leave the district blank.",
    "district": "Same as province, plus projects whose province is known but that span districts (Pancheshwar, Sapta Koshi).",
    "river": "Wikipedia rows carry no river; only DoED registers name one.",
    "developer": "Wikipedia rows for future projects often list no owner; DoED rows always name a promoter.",
    "type": "No source publishes run-of-river / peaking / storage; it is only read from explicit words in project names.",
}


def summary(df: pd.DataFrame) -> list[dict]:
    total_mw = df["capacity_mw"].sum()
    out = []
    for key, (label, is_unknown) in DIMENSIONS.items():
        u = df[is_unknown(df)]
        by_source = (u.groupby("data_reliability").agg(projects=("project_id", "count"), mw=("capacity_mw", "sum"))
                       .sort_values("mw", ascending=False).reset_index())
        by_stage = u.groupby("status").agg(projects=("project_id", "count"), mw=("capacity_mw", "sum")).reset_index()
        out.append({
            "dimension": key, "label": label, "projects": int(len(u)), "share_of_projects_pct": round(len(u) / len(df) * 100, 1),
            "mw": round(float(u["capacity_mw"].sum()), 1), "share_of_mw_pct": round(float(u["capacity_mw"].sum()) / total_mw * 100, 1) if total_mw else 0.0,
            "why": WHY[key],
            "by_source": [{"source": SOURCE_LABEL.get(r.data_reliability, r.data_reliability), "projects": int(r.projects), "mw": round(float(r.mw), 1)}
                          for r in by_source.itertuples()],
            "by_stage": [{"stage": r.status, "projects": int(r.projects), "mw": round(float(r.mw), 1)} for r in by_stage.itertuples()],
        })
    return out


def unknown_projects(df: pd.DataFrame, dimension: str, limit: int = 30) -> pd.DataFrame:
    """The projects behind an 'Unknown' bar, biggest first, with what IS known about each."""
    _, is_unknown = DIMENSIONS[dimension]
    u = df[is_unknown(df)].sort_values("capacity_mw", ascending=False).head(limit)
    return u[["project_id", "name", "capacity_mw", "status", "province", "district", "river", "developer", "data_reliability"]]
