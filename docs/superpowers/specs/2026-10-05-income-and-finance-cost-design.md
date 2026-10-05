# Income forecast and finance-cost analysis: design

Date: 2026-10-05. Branch: `ppa-data-and-dashboard-redesign`. Status: draft for review.

## Intent

Add an "Income and finance" section to the dashboard that answers three questions for the 111 NEPSE-listed
hydropower companies:

1. How much of its installed capacity does each company use in the wet and dry seasons?
2. What quarterly income does that production imply, and which companies earn the most?
3. What finance cost is each company carrying, how will it fall over time, and what would lower interest
   rates do to net profit, EPS and book value per share?

Success: a reviewer can open one page, see a ranked table, change three scenario controls, and trace every number
back to either a stored fact or a named assumption.

Said by the user: forecast basis is "calibrated to reported sales"; plants without a verified PPA rate stay in the
ranking with a flag; defaults are rate change -2 pp, repayment 8% of the current loan per year, retention 70%,
horizon 5 years; loans are allocated to plants by MW share.

Assumptions made by me: new page rather than changes to `/analytics`; the horizon for the income forecast is four
fiscal quarters; plants under construction are out of scope.

## What the data actually holds (checked 2026-10-05)

- `company_financials` has detailed fields (loans, finance cost, electricity sales, tax provision, EPS, book
  value per share, paid-up capital, reserves) for 107 companies, **one period each**: FY 2082/83, quarter 4
  (full year, ended mid-July 2026).
- Quarterly rows carry only **cumulative** net profit. They are a shape check, not a sales series.
- `projects.debt_percentage`, `estimated_project_cost_npr` and `project_financials` are empty, so there is no
  per-project debt or interest. Company-level figures are allocated to plants (labelled as an allocation).
- Shares for EPS are paid-up capital / 100. `listed_shares` covers only the listed part and does not reproduce
  the reported EPS.
- Verified PPA rates exist for 94 companies (`company_facts`, project-level where a report names a plant).

## Architecture

Approach A: two new modules that compute from the database on request, thin API endpoints, one new page.
Nothing is precomputed or stored, so figures cannot go stale when a rate or financial changes.

| Unit | Purpose | Depends on |
|---|---|---|
| `src/analytics/income.py` | seasonal production, quarterly income, calibration, ranking | models, config, `financials.blended_tariff` |
| `src/analytics/finance_cost.py` | implied rate, finance-cost path, low-rate scenario, EPS and book value impact, plant allocation | models, config |
| `src/dashboard.py` | `/income` page, `/api/income/forecast`, `/api/finance/impact` | the two modules |
| `dashboards/templates/income.html` + JS in `app.js` | page, controls, charts | existing Chart.js and tokens |

## Section 1: production and income (`income.py`)

Per listed company with at least one operating plant:

1. **Seasonal capacity factors.** From the config assumptions (annual factor 0.55, dry share of energy 0.30) and a
   fixed season calendar: wet = mid-Apr to mid-Dec, 245 days; dry = mid-Dec to mid-Apr, 120 days.
   Dry factor = 0.55 x 0.30 / (120/365) = 0.50; wet factor = 0.55 x 0.70 / (245/365) = 0.57.
   Usage % for a season is energy / (MW x hours in season), i.e. the calibrated seasonal factor.
   **Average usage** (added at the user's request) combines the two seasons weighted by days:
   (245 x wet + 120 x dry) / 365, which equals total energy / (MW x 8760 h), the annual capacity factor. A plain
   mean of the two percentages is not used because it would overweight the shorter dry season. Wet, dry and
   average usage are all shown.
2. **Quarter split by days** (fiscal quarters from Shrawan): Q1 92 days wet; Q2 89 days = 60 wet + 29 dry;
   Q3 91 days dry; Q4 93 days wet. Totals 365; wet 245, dry 120.
3. **Income per plant per quarter** = wet energy x wet rate + dry energy x dry rate. Rate = project-level verified
   fact, else company-level verified fact, else the config fallback tariff (4.80 / 8.40) with the flag
   `assumed_rate`.
4. **Calibration.** One annual factor per company = reported FY 2082/83 `electricity_sales_npr` / modelled income for
   the same year. If any operating plant began in or after the reported year's starting calendar year, calibration
   is skipped (factor 1.0, flag `partial_year`), because reported sales include that plant's partial-year revenue.
   The factor is clipped to 0.3 - 1.5 and the clip is flagged. No reported sales: factor 1.0, flagged
   `no_reported_sales`. The factor and flags are shown on the page.
5. **Horizon**: the four fiscal quarters of FY 2083/84 (the year after the latest reported full year).
6. **Ranking**: companies sorted by forecast annual income (sum of the four quarters).

Sanity check, not used in the numbers: the shape of the cumulative quarterly net profit is compared with the modelled
quarterly shape and reported on the page as a rough agreement indicator.

Deliberately not in v1: per-company seasonal overrides taken from rating reports. They are stated for only a few
companies and are not stored; the config defaults apply to everyone.

## Section 2: finance cost and low-rate impact (`finance_cost.py`)

From each company's latest full-year row:

1. **Position.** Implied rate = finance cost / year-end loans (the average balance is not available, so this is an
   approximation). Flagged when loans are tiny, finance cost is zero, or the rate falls outside 2 - 20%. Effective
   tax rate = tax provision / (net profit + tax provision), often low because of tax holidays. Baseline EPS and book
   value per share are the reported figures.
2. **Base path.** The loan falls by the repayment rate each year (default 8% of the current balance per year,
   about a 12-year amortisation); finance cost = rate x balance.
3. **Scenario.** The rate shifts by the chosen pp (default -2, floored at 0%). Per year: finance-cost saving versus
   the base path; net profit change = saving x (1 - effective tax rate); EPS change = net-profit change / shares;
   book value per share change = cumulative net-profit change x retention (default 70%) / shares. Horizon 5 years.
4. **Plant view.** Loans and finance cost are spread across a company's operating plants by MW share and labelled
   "allocated, not reported".
5. **Ranking.** Finance-cost burden (finance cost / modelled annual income) and interest cover
   ((net profit + tax + finance cost) / finance cost), shown beside the income ranking.

Out of scope: loans on plants under construction (their cost starts when they commission), refinancing fees,
floating versus fixed loan splits.

## Section 3: API, page, tests

**API** (all return JSON; invalid input returns 400 like the existing endpoints):

- `GET /api/income/forecast` returns the assumptions, the four quarter labels, and per company: symbol, name,
  capacity, wet, dry and average usage %, wet and dry energy (GWh), quarterly income, annual income, rank, calibration factor
  and flags.
- `GET /api/finance/impact?rate_delta_pp=-2&repay_pct=8&retention_pct=70&years=5` returns per company the position,
  the base and scenario paths, EPS and book value per share before and after, and the per-plant allocation.
  Ranges: rate_delta_pp -10 to +10, repay_pct 0 to 50, retention_pct 0 to 100, years 1 to 10.

**Page** `/income`, linked from the sidebar: (a) summary tiles for total forecast income and total capacity usage;
(b) wet versus dry usage chart and a quarterly income chart; (c) ranked company table with calibration and
`assumed_rate` flags visible, not hidden in tooltips; (d) finance panel with three controls that re-query the API;
(e) a per-company drill-down for plant allocation. Light and dark themes follow the existing tokens. Tables stay
accessible. The page states clearly that figures are estimates.

**Tests** (pytest, synthetic in-memory database as in `conftest.py`):

- quarter day splits sum to 365 and wet days to 245; seasonal energy sums to the annual figure;
- average usage equals total energy / (MW x 8760) and lies between the wet and dry usage;
- rate resolution order (project, company, fallback) and the `assumed_rate` flag;
- calibration: scaling, clipping, `partial_year` skip, `no_reported_sales`;
- ranking order and stability for ties;
- finance maths: implied rate, base path, rate shift floored at 0%, tax, EPS and book value changes, zero-loan
  and zero-finance-cost cases, plant allocation sums to the company figure;
- API: contract, defaults, 400 on out-of-range input; page renders.

## Risks

- One reported year per company means the calibration factor rests on a single observation; the page shows it.
- Year-end loans make the implied rate approximate; flagged, not hidden.
- Fallback tariffs for plants without a verified rate affect many ranks; those quarters carry the flag.
- The seasonal calendar uses mid-month boundaries; real BS month lengths differ by a day or two.
