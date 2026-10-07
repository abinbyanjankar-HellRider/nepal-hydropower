"""Multi-year and quarter-by-quarter comparison for one company.

What the data supports (and what it does not):
  * Net profit is known for many past quarters (ShareSansar results announcements): reported CUMULATIVE year to date, so a
    standalone quarter is the difference between consecutive quarters. That gives fiscal-year and quarter comparisons.
  * Balance-sheet / ratio items (sales, finance cost, loans, ROE, EPS, ...) are only published in full for the latest
    quarter. Earlier years show them only if a full report row was stored (each `reports financials` run adds the newest
    one) or loaded with `import-csv --kind company-financials`. Missing values stay blank; nothing is estimated.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CompanyFinancial


def _pct(cur: float | None, prev: float | None) -> float | None:
    """Percentage change; only meaningful when the base is positive (a change from a loss is not a percentage)."""
    if cur is None or prev is None or prev <= 0:
        return None
    return round((cur - prev) / prev * 100, 1)


def _fmt_npr(v: float) -> str:
    a = abs(v)
    return f"NPR {v / 1e9:,.2f} bn" if a >= 1e9 else f"NPR {v / 1e6:,.1f} m"


def _prev_fy(fy: str) -> str:
    start = int(fy.split("/")[0]) - 1
    return f"{start}/{(start + 1) % 100:02d}"


def _full_metrics(r: CompanyFinancial | None) -> dict:
    """Ratios derived from one full report row. Anything missing stays None."""
    if r is None:
        return {}
    equity = (r.paid_up_capital_npr or 0) + (r.reserves_npr or 0) if r.paid_up_capital_npr is not None else None
    pre_tax = r.net_profit_npr + r.tax_provision_npr if r.net_profit_npr is not None and r.tax_provision_npr is not None else None
    return {
        "period": f"FY {r.fiscal_year} Q{r.quarter}", "electricity_sales_npr": r.electricity_sales_npr,
        "operating_income_npr": r.operating_income_npr, "finance_cost_npr": r.finance_cost_npr, "loans_npr": r.loans_npr,
        "debt_to_equity": round(r.loans_npr / equity, 2) if r.loans_npr is not None and equity and equity > 0 else None,
        "net_margin_pct": round(r.net_profit_npr / r.operating_income_npr * 100, 1)
        if r.net_profit_npr is not None and r.operating_income_npr else None,
        "finance_cost_pct_of_sales": round(r.finance_cost_npr / r.electricity_sales_npr * 100, 1)
        if r.finance_cost_npr is not None and r.electricity_sales_npr else None,
        "effective_tax_rate_pct": round(r.tax_provision_npr / pre_tax * 100, 1) if pre_tax and pre_tax > 0 else None,
        "interest_cover": round((pre_tax + r.finance_cost_npr) / r.finance_cost_npr, 1)
        if pre_tax is not None and r.finance_cost_npr and r.finance_cost_npr > 0 else None,
        "roe_pct": r.roe_pct, "roa_pct": r.roa_pct, "eps": r.eps, "networth_per_share": r.networth_per_share,
        "paid_up_capital_npr": r.paid_up_capital_npr,
    }


def build_trend(session: Session, company_id: int, years: int = 5) -> dict:
    rows = list(session.scalars(select(CompanyFinancial).where(CompanyFinancial.company_id == company_id)))
    ytd: dict[tuple[str, int], float] = {(r.fiscal_year, r.quarter): r.net_profit_npr for r in rows if r.net_profit_npr is not None}
    full = {(r.fiscal_year, r.quarter): r for r in rows if r.loans_npr is not None or r.paid_up_capital_npr is not None}
    if not ytd:
        return {"available": False, "years": [], "quarters": {}, "annual": [], "latest": None,
                "note": "No quarterly results loaded for this company."}
    fys = sorted({fy for fy, _ in ytd}, reverse=True)[:years]

    def standalone(fy: str, q: int) -> float | None:
        cur = ytd.get((fy, q))
        if cur is None:
            return None
        if q == 1:
            return cur
        prev = ytd.get((fy, q - 1))
        return cur - prev if prev is not None else None

    quarters: dict[str, dict[int, dict]] = {}
    for fy in fys:
        quarters[fy] = {}
        for q in (1, 2, 3, 4):
            sa, last_sa = standalone(fy, q), standalone(_prev_fy(fy), q)
            cum, last_cum = ytd.get((fy, q)), ytd.get((_prev_fy(fy), q))
            quarters[fy][q] = {"ytd_npr": cum, "standalone_npr": sa, "ytd_yoy_pct": _pct(cum, last_cum),
                               "standalone_yoy_pct": _pct(sa, last_sa)}

    latest_full = full.get(max(full, default=None)) if full else None
    latest_capital = latest_full.paid_up_capital_npr if latest_full else None
    annual = []
    for fy in fys:
        q_done = max((q for q in (1, 2, 3, 4) if (fy, q) in ytd), default=None)
        profit = ytd.get((fy, 4)) if (fy, 4) in ytd else ytd.get((fy, q_done)) if q_done else None
        prev_profit = ytd.get((_prev_fy(fy), 4))
        year_row = full.get((fy, 4)) or next((full[(fy, q)] for q in (3, 2, 1) if (fy, q) in full), None)
        annual.append({
            "fiscal_year": fy, "net_profit_npr": profit, "complete": (fy, 4) in ytd, "quarters_reported": q_done,
            "yoy_pct": _pct(profit, prev_profit) if (fy, 4) in ytd else None,
            # earnings relative to TODAY's paid-up capital: comparable across years, but bonus/right issues make older years
            # look smaller than they were per share at the time
            "profit_pct_of_current_capital": round(profit / latest_capital * 100, 1) if profit is not None and latest_capital else None,
            "full_report": _full_metrics(year_row)})

    latest_key = max(full) if full else max(ytd)
    return {"available": True, "years": fys, "quarters": quarters, "annual": annual,
            "latest": _latest_analysis(latest_key, ytd, full.get(latest_key), standalone),
            "current_fiscal_year": fys[0],
            "note": ("Net profit is reported cumulatively (year to date); a standalone quarter is the difference between "
                     "consecutive quarters. Full balance-sheet ratios exist only for periods with a stored full report.")}


def _latest_analysis(key: tuple[str, int], ytd: dict, row: CompanyFinancial | None, standalone) -> dict:
    fy, q = key
    prev_fy = _prev_fy(fy)
    cur, last_year = ytd.get(key), ytd.get((prev_fy, q))
    sa, sa_last = standalone(fy, q), standalone(prev_fy, q)
    prev_q = (fy, q - 1) if q > 1 else (prev_fy, 4)
    sa_prev = standalone(*prev_q)
    m = _full_metrics(row)
    bullets: list[str] = []
    if cur is not None:
        s = f"Net profit for FY {fy} Q{q} (year to date) is {_fmt_npr(cur)}"
        bullets.append(s + (f", {_pct(cur, last_year):+.1f}% against {_fmt_npr(last_year)} for the same period last year." if _pct(cur, last_year) is not None
                            else (f"; the same period last year was {_fmt_npr(last_year)} so a percentage change is not meaningful." if last_year is not None
                                  else "; no figure for the same period last year is on record.")))
    if sa is not None and q > 1:
        s = f"Standalone quarter profit is {_fmt_npr(sa)}"
        if sa_prev is not None:
            s += f" versus {_fmt_npr(sa_prev)} in the previous quarter"
        if sa_last is not None:
            s += f" and {_fmt_npr(sa_last)} in the same quarter last year"
        bullets.append(s + ".")
    if m.get("net_margin_pct") is not None:
        bullets.append(f"Net margin is {m['net_margin_pct']}% of operating income (net profit / operating income).")
    if m.get("finance_cost_npr") is not None and m.get("finance_cost_pct_of_sales") is not None:
        bullets.append(f"Finance cost of {_fmt_npr(m['finance_cost_npr'])} takes {m['finance_cost_pct_of_sales']}% of electricity sales"
                       + (f"; interest cover is {m['interest_cover']}x (pre-tax profit plus finance cost, over finance cost)." if m.get("interest_cover") else "."))
    if m.get("loans_npr") is not None:
        bullets.append(f"Loans and long-term liabilities are {_fmt_npr(m['loans_npr'])}"
                       + (f", a debt-to-equity ratio of {m['debt_to_equity']}." if m.get("debt_to_equity") is not None else "."))
    if m.get("effective_tax_rate_pct") is not None:
        bullets.append(f"The effective tax rate is {m['effective_tax_rate_pct']}% of pre-tax profit.")
    return {"fiscal_year": fy, "quarter": q, "period": f"FY {fy} Q{q}", "has_full_report": row is not None,
            "net_profit_ytd_npr": cur, "same_period_last_year_npr": last_year, "yoy_pct": _pct(cur, last_year),
            "standalone_npr": sa, "previous_quarter_standalone_npr": sa_prev, "same_quarter_last_year_standalone_npr": sa_last,
            "metrics": m, "bullets": bullets,
            "comparison_note": None if row is None else
            "Sales, finance cost, loans and ratios are published in full only for the latest quarter, so their prior-year "
            "comparison appears once earlier full reports are stored or loaded (import-csv --kind company-financials)."}
