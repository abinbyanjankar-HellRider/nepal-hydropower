# Nepal Hydropower Database

A database, analytics layer, web dashboard and Excel exporter for hydropower projects in Nepal, built from
public registers and linked to NEPSE-listed companies.

**Current data (from the last sync):** 816 projects — 223 operational (4,010 MW), 221 under construction (9,487 MW),
75 licensed, 297 planned/survey-stage — with 780 mapped locations and all 111 NEPSE-listed hydropower companies (110 of them are promoter of record on at least one project; only TVCL is not, because its plant is registered to NEA in DoED).

## Where the data comes from (and how far to trust it)

| Source | What it provides | Trust |
|---|---|---|
| **DoED** licence registers (`doed.gov.np`) — Power Plants, Generation Licence, Survey Licence pages | Name, capacity, river, promoter, licence no./dates, location, coordinates, COD (Bikram Sambat dates converted to AD) | **Authoritative** for licences; COD dates occasionally disagree with other sources |
| **Wikipedia** "List of power stations in Nepal" | Stage (operational / under construction / upcoming), owner, province, expected completion year | Crowd-sourced. Fills gaps only, never overwrites DoED. Contains errors (e.g. one project listed in the wrong district) |
| **Merolagani** NEPSE sector listing | 111 listed hydropower companies: symbol, listed shares, paid-up value | Third-party mirror of the NEPSE listing |
| **RSS** (Online Khabar, Rising Nepal, Kathmandu Post) | News, attached to projects whose full name appears in the article | Heuristic name matching |
| Hand-entered seed (`src/seed_data.py`) | 23 well-known projects, used only to bootstrap | **Unverified**, marked `seed-unverified`; almost all are overwritten by scraped data |

Every project row carries `data_reliability` (`doed`, `wikipedia`, `seed-unverified`, `csv-import`, `api`) and
`data_source`, and the dashboard flags low-confidence rows.

### What is *not* in the data
- **Financials** (revenue, profit, IRR by fiscal year), **shareholder structure**, **technical details** (turbines,
  head, tunnel lengths) and **PPA rates** have no public bulk source, so those tables start empty. The schema and
  CSV importers exist for you to load them from annual reports / SEBON filings (see below).
- **NEPSE exposure** counts only projects where a listed company is the promoter of record in DoED, matched by
  exact normalised company name. Matching is strict on purpose (a wrong symbol link is worse than a missed one),
  so only 1 listed company (TVCL) currently shows no projects, because DoED registers its plant to NEA, not to TVCL. `analyze nepse --suggest` proposes candidates; confirm
  real ones in `data/company_aliases.csv` (`symbol,company_name`) and re-run `sync --source nepse`.
  Holdings through subsidiaries or minority stakes are invisible until loaded as shareholders.
- **Status nuance:** DoED does not say whether a generation-licensed project has started building, so those are
  `Licensed`; only projects on Wikipedia's under-construction list are `Under Construction`.
- Revenue figures from `analyze revenue` / the Financial sheet are **estimates from assumed tariffs and capacity
  factors** (`src/config.py`), not reported revenue.

## Quick start

```bash
pip install -r requirements.txt
python main.py load-sample     # districts, rivers, reference companies
python main.py sync            # scrape DoED + Wikipedia + NEPSE and merge (about 10 s, idempotent)
python main.py summary         # headline numbers
python main.py web             # dashboard at http://127.0.0.1:5000
```

`python main.py load-seed` is optional (the unverified bootstrap rows). `python main.py refresh` runs, in order: backup, sync (DoED, Wikipedia, NEPSE), resolve-locations, quarterly financials,
news, validation and Excel export, and is suitable for a daily scheduled task (Windows Task Scheduler / cron). Every stage
runs even if an earlier one fails, and the exit code is 1 if any did, so a scheduler can alert you. Add `--with-reports` to
also find and download new report PDFs (annual first, then quarterly) and extract facts; `--skip-network` runs only the
local stages. Because the financials stage stores each company's newest full report, the multi-year trend tables grow with
every run.

## Commands

| Command | Purpose |
|---|---|
| `init [--drop]` · `load-sample` · `load-seed` · `stats` | Database setup and row counts |
| `sync [--source all\|doed\|wikipedia\|nepse]` | Scrape and merge. Raw tables are saved to `data/raw/` for traceability; source disagreements go to `data/processed/sync_conflicts.txt` |
| `update-news` | Fetch RSS news and attach it to mentioned projects and NEPSE-listed companies (by name or stock symbol) |
| `validate-data [--details]` | Data-quality audit → `data/processed/validation_report.csv` |
| `query [--status S] [--min-mw N]` | List projects |
| `summary` | Overview |
| `analyze capacity --by status\|province\|district\|river\|type\|developer\|size [--status S]` | Capacity grouped by a dimension |
| `analyze top · timeline · pipeline · licences · developers` | Largest projects, MW added per year, construction pipeline, expired licences, developer ranking |
| `analyze nepse [--suggest]` | NEPSE-listed companies and their capacity; `--suggest` proposes promoter matches for review |
| `analyze company CHCL` | A company's projects by NEPSE symbol or name fragment |
| `analyze forecast [--delay-years N] [--completion-rate R]` | Capacity scenario if the construction pipeline lands (only the 14 of 221 under-construction projects that state an expected year) |
| `analyze revenue [--capacity-factor …]` | **Estimated** annual revenue, with overridable assumptions |
| `analyze project HP_001` | Everything known about one project |
| `import-csv FILE --kind projects\|financials\|shareholders\|facts\|company-financials` | Load your own verified data |
| `export [--format master\|operational\|construction\|planned\|financial\|technical\|nepse\|all]` | Excel workbooks in `exports/generated/` |
| `web [--host H --port P]` | Dashboard + JSON API |
| `resolve-locations` | Fill missing district/province/river/type from traceable evidence |
| `backup` · `refresh` | Copy the DB · full update cycle |

### CSV formats
- **projects:** any `Project` columns plus friendly `district`, `river`, `developer`, `owner` names, e.g.
  `project_id,project_name_en,capacity_mw,status,district,developer`. Existing IDs are updated, bad rows are
  reported without aborting the import.
- **financials:** `project_id,fiscal_year,energy_generated_gwh,revenue_npr,operating_cost_npr,net_profit_npr,capacity_factor_percentage,irr_percentage,source`
- **shareholders:** `project_id,shareholder_name,shareholder_type,stock_symbol,stake_percentage`

## Dashboard and API

Pages: **Overview** (KPIs, charts, news), **Map** (Leaflet, filter by stage), **Projects** (search, filters, sort,
paging, Excel download), **Project detail**, **Companies** (developer ranking, NEPSE exposure), **Company
profile** (now includes a "Latest news" section for that company), **News** (hydropower project news and
NEPSE-listed company news side by side, each item linked to its project and/or company profile), **Analytics**.
Map tiles and chart libraries load from CDNs, so the browser needs internet access.

JSON API (all under `/api`): `projects` (filters: `status`, `province`, `district`, `q`, `developer`, `symbol`,
`capacity_min`, `capacity_max`, `sort`, `order`, `limit`, `offset`), `projects/<id>`, `projects/statistics`,
`geojson`, `companies`, `companies/<id>`, `companies/<id>/profile` (includes a `news` list), `news` (filters:
`scope=all|projects|companies`, `limit`), `analytics/capacity`, `analytics/licences`, `export/excel?format=…`.
`POST /api/projects/add` is **disabled unless `ADMIN_TOKEN` is set**, then requires an `X-Admin-Token` header.

News matching (`src/collectors/news_scraper.py`): RSS items from Online Khabar, The Rising Nepal and Kathmandu
Post are matched by name to tracked projects, and separately matched (by company name or stock symbol) to
NEPSE-listed companies. An article naming both a project and its own developer is stored once, against the
project — the company still sees it via that project. An article naming only a company (no specific project,
e.g. a market/dividend item) is stored directly against that company. Short/ambiguous names (under 6 folded
characters) and stock symbols under 3 characters are skipped to avoid false matches in free text.

The dashboard builds its DOM with `textContent` only, so hostile text in scraped names cannot inject markup.

## Company profiles and reports

Every listed company has a profile page (`/companies/<id>`) and a row in the Companies table showing its projects by
stage (operating / under construction / licensed / planned) and the terms behind them:

| Shown | Where it comes from | Reliability |
|---|---|---|
| **Current loans**, **finance cost**, sales, net profit, EPS, ROE | ShareSansar's latest quarterly report ("Loans & long-term liabilities", "Financial expenses"), converted from Rs '000 | Reported figures. Finance cost is year-to-date for the stated period |
| **Tax-free or not** | Effective tax rate = tax provision / pre-tax profit. Under 2% reads "Tax-free (holiday)", under 15% "Partly exempt", else "Taxable" | Inferred from reported numbers, **not a legal determination**. Needs a profitable period, else "Unknown" |
| **Cost per MW** | Reported project costs when known; otherwise a *capital-employed proxy* (paid-up capital + reserves + loans) / (operating + building MW) | A rough guide. Hidden when outside NPR 60-700 m/MW (holding companies, incomplete capacity records) |
| **PPA rate** | Only a **verified** value (`import-csv --kind facts`) is shown as the headline. Values regex-read from reports appear as *candidates to check*, with page and snippet | Auto-extraction is noisy: one "PPA rate" it found was actually a retail tariff |
| **Outlook** | Scenario arithmetic: pipeline MW x the company's own revenue per MW (or the assumed tariff), and capex at its cost per MW | Not a valuation or investment advice |

### Financial trends on each profile
Each company page also shows, for the last five fiscal years:
- **Quarterly net profit, current fiscal year vs previous years:** a Q1-Q4 chart and table (toggle standalone quarter or
  cumulative year to date) with the change against the same quarter of the previous year.
- **Latest quarterly report analysis:** the newest report against the same period last year, the previous quarter and a
  plain-language summary (margin, finance-cost burden, interest cover, debt-to-equity, tax rate).
- **Five-year table:** annual net profit and growth, profit as a share of today's paid-up capital, and sales, finance cost,
  loans, debt/equity, ROE, EPS and net worth per share for years that have a full report.

What is complete and what is not: net profit is known for many past quarters for **every** listed company (about 17
quarters on average, back to FY 2070/71) because ShareSansar posts a results headline each quarter. Those figures are
cumulative year to date, so a standalone quarter is the difference between consecutive quarters. Sales, finance cost, loans
and the ratios are published in full for the **latest quarter only**, so earlier years show a dash there rather than an
estimate; percentage changes are blank when last year's base was zero or a loss. Neither ShareSansar nor Merolagani offers
free multi-year statements, and the downloaded PDFs are too irregular to parse reliably (only 3 of 14 sampled quarterly
PDFs had readable figure lines, 1 of 12 annual reports had a multi-year ratio table). Each `reports financials` run adds
the newest full report, so history accumulates; you can backfill years from annual reports with:
`python main.py import-csv history.csv --kind company-financials` (columns `symbol, fiscal_year, quarter, paid_up_capital_npr,
reserves_npr, loans_npr, electricity_sales_npr, operating_income_npr, finance_cost_npr, tax_provision_npr, net_profit_npr,
eps, networth_per_share, roe_pct, roa_pct, source`; amounts in NPR, quarter 4 for full-year figures).

### Reviewing extracted facts
Auto-extracted numbers are only leads. Read the sentence shown with each one (company page > "Facts read from reports"),
then record your decision so it is traceable:

```bash
python main.py reports verify-fact 147 --project HP_001 --note "board-approved revised cost, excluding interest during construction"
python main.py reports reject-fact 3 --reason "retail commercial tariff, not a PPA rate"
```
A verified fact linked to a project feeds that project's cost and the company's cost per MW (even when a different company's
report supplied it: BPC's report gives Kabeli-A's cost). A rejected fact is kept, renamed `rejected_...`, and ignored.
Review done so far: all 19 cost / PPA candidates were read against their source text. **8 verified**: Upper Tamakoshi NPR
49,296 m (revised, excluding interest during construction, NPR 108 m/MW), Mai Beni NPR 2,000 m (NPR 210 m/MW), Kabeli-A NPR
7,520 m (NPR 200 m/MW), and Andhi Khola's NPR 1,400 m upgrade (kept unlinked: an upgrade cost, not a plant cost).
**11 rejected**: a retail tariff mistaken for a PPA rate, five copies of a 7 MW solar project's cost, and five superseded
original estimates. **44 companies now have a verified PPA rate** (a sixth batch of 1 at a 4 / 7 base tariff: Shiva Shree, `data/verified_facts_ppa_ratings6.csv`; a fifth batch of 3 at the standard rates: Maya Khola, Mandu, Balephi; loaded from `data/verified_facts_ppa_ratings5.csv`; a fourth batch of 5 at the standard rates: Nyadi, Modi Energy, Ridge Line Energy, Taksar Pikhuwa Khola, Him Star Urja; loaded from `data/verified_facts_ppa_ratings4.csv`; a third batch of 7 from rating reports: Sanigad Hydro, Kalanga Hydro, Panchthar Power, Arun Kabeli, Mountain Hydro Nepal and Bungal Hydro at the standard 4.80 / 8.40, and Himalayan Hydropower at its older 4 / 7 base tariff; loaded from `data/verified_facts_ppa_ratings3.csv`; a second batch of 14 from rating reports: Rasuwagadhi, Madhya Bhotekoshi, Sanima Middle Tamor, Sahas Urja, Solu, Kalinchock, Dordi Khola, United Modi, Peoples, Upper Solu, Mid Solu, Chhyangdi at the standard rates; Mailung Khola at its present 3.72 / 5.27 and Sanima Mai at its present 5.08 / 8.89, both after escalations; loaded from `data/verified_facts_ppa_ratings2.csv`). The first 14 are: Thirteen small run-of-river IPPs (Ingwa, Menchhiyam, Upper Hewa, Dolti, Makar Jitumaya, Joshi, Kutheli Bukhari, Dibyashwari, Sagarmatha, Synergy, Rawa, Manakamana Engineering, Terhathum) are on NEA's standard NPR 4.80 wet / 8.40 dry per kWh, with 3% annual escalation on the base tariff, as stated in ICRA Nepal, CARE Ratings Nepal or Infomerics reports (each fact cites report and page; copies in `data/raw/ratings/`; loaded from `data/verified_facts_ppa_ratings.csv`). Joshi is mixed (985 kW at 4.00/7.00). Upper Tamakoshi's own, from an ICRA Nepal rating report (wet NPR 3.63 / dry NPR 6.96 per kWh, 3% escalation for nine years; the report repeats 6.96 for the escalated dry rate, so treat the dry figure with care). NEA's standard run-of-river PPA rates of NPR 8.40 (dry) and NPR 4.80 (wet) per kWh, which the revenue estimates assume, were confirmed by news reports (e.g. Kathmandu Post, 10 Feb 2023).

### Getting the reports
```bash
python main.py reports run                 # everything, in this order:
#   1. company info (website/email)  2. find PDFs on company websites  3. download ALL annual reports
#   4. download ALL quarterly reports  5. structured quarterly financials (ShareSansar)  6. extract facts
python main.py reports guess-websites [--search]   # find websites: guess domains, optionally web-search; each must pass a page-content check
python main.py reports status              # coverage table
python main.py reports set-website SYMBOL URL      # tell it where a company's site is
python main.py reports add-url SYMBOL URL --kind annual --fy 2080/81   # register a PDF you know of
python main.py reports fetch|discover|extract|financials [--symbol S] [--limit N]   # stages individually
python main.py import-csv verified_facts.csv --kind facts   # symbol,project_id,fact_type,value_num,value_text,unit,fiscal_year,source
```
Files are stored in `data/reports/<SYMBOL>/<annual|quarterly>/` (size-capped at 60 MB, verified as PDFs, hashed), and
every found/failed link is recorded in `company_reports`.

**Coverage achieved (last full run).** Structured quarterly financials for all 111 listed companies (104 with a loan
figure, 81 with an inferable tax status, 62 with a plausible cost per MW). **106 of 111 companies have a website on
record** (19 from ShareSansar emails, 52 from verified domain guesses, 20 from web search verified against the page,
4 named as official by web search but not machine-verifiable because of a bot-wall, timeout or JavaScript-only page,
1 set manually). From those sites **294 annual reports (53 companies) and 362 quarterly reports (44 companies)** were
downloaded; 5 links (all on one company's site) are dead. Extraction found 148 tax-holiday statements (22 companies),
18 project costs (3 companies) and 1 PPA candidate: about 19% of annual PDFs are scanned images, and most IPP annual
reports simply do not state the PPA rate in prose. Expect to add PPA rates and project costs yourself with
`import-csv --kind facts` (each stays traceable to the report and page you cite).

**What the sources really offer.** Neither ShareSansar nor Merolagani hosts report PDFs: ShareSansar gives structured
quarterly figures (used above) and Merolagani shows filings through an image viewer. Annual and quarterly *PDFs* therefore
come only from each company's own website, so coverage depends on the company publishing them (see "Known limitations").

### What "Unknown" means, and filling it in
Some projects have no location, river, developer or type in any source, so the analytics group them as "Unknown". The
Analytics page has a **"What is Unknown?"** panel showing how many projects and MW that is for each attribute, why, and
the projects behind it. Before any filling: 77 projects (15,020 MW, 33% of capacity) had no province: 63 Wikipedia-only
rows with no location at all, and 14 DoED rows with coordinates but a blank district.

`python main.py resolve-locations [--dry-run] [--no-geocode]` fills gaps only from traceable evidence, in this order:
manual overrides (`data/location_overrides.csv`: `project_id,district,province,river,source`), reverse geocoding of the
project's own coordinates (OpenStreetMap Nominatim, 1 request/second, cached), a same-named project in DoED or in DoED's project-bank lists (government-studied, under-study and generation-licence
applications), a district
named in the project name, a short curated list of well-known projects, and river / project type from explicit words in the
name ("Storage", "PRoR"). It never overwrites a value a source supplied, is idempotent, and writes how each value was
obtained into the project's "Inferred values" field, shown on its page. After running it: 6 projects (29.5 MW) still have
no province, 748 of 816 still have no type, and 53 (13,893 MW) still have no developer. Those are genuinely unknown, not
guesses. Add corrections to the overrides file to shrink them further.

## How merging works (`src/collectors/sync.py`)

1. Match on **licence number + type** (exact key), else normalised name + capacity, else looser name/spelling
   matching. Names are folded for Nepali transliteration (Trisuli = Trishuli, Budi = Budhi).
2. Names differing by a discriminator (**Upper/Lower/Middle/Super, 1/2/3, A/B**) are never merged.
3. DoED overwrites; Wikipedia only fills empty fields. A project's stage only moves forward.
4. Disagreements (capacity >10%, commissioning year) are logged, not silently resolved: review
   `data/processed/sync_conflicts.txt` (e.g. Chilime's COD is 2003 on Wikipedia but 2008 in DoED).

Re-running `sync` is idempotent (0 new rows when sources are unchanged).

## Layout

```
main.py                     CLI entry point
src/
  config.py models.py database.py seed_data.py
  collectors/  web_scraper.py (DoED, Wikipedia)  nepse_scraper.py  news_scraper.py  sync.py  manual_import.py
               sharesansar.py (quarterly financials)  company_reports.py (find/download report PDFs)
  processors/  cleaners.py (names, BS dates, DMS coordinates, districts)  validators.py  report_extract.py
  analytics/   data.py technical.py financials.py ownership.py company_profiles.py company_trends.py
  exports/     excel_builder.py
  dashboard.py cli.py cli_analyze.py cli_reports.py
dashboards/    templates/ static/
tests/         81 offline tests (pytest)
data/          projects.db, raw/ (scrape snapshots), processed/ (reports)
```

## Development

```bash
python -m pytest tests -q      # no network needed
```

Add a field: add the column to `src/models.py`, recreate the SQLite file (`init --drop`, then `sync`) or use Alembic
for PostgreSQL, extend the scraper/CSV columns. Set `DATABASE_URL` for PostgreSQL (`psycopg2` is not in
`requirements.txt`; install it yourself — PostgreSQL support is untested).

**Docker** (`Dockerfile`, `docker-compose.yml`) is provided but has not been built or run in this environment.

## Known limitations
- **Report coverage is partial:** 15 listed companies have no website that could be found (mostly single-project
  companies with only a Gmail address or a Facebook page), so no report files; the others' sites may publish only some
  years. Use `reports set-website` / `reports add-url` to extend it.
- **Auto-extracted facts are candidates.** Only verified values become a headline PPA rate; auto values are shown with
  their page and snippet and may not refer to the company's own projects (a solar project cost sits beside a hydro one).
- **Project-level costs are mostly missing**, so cost per MW is usually the capital-employed proxy; treat it as a guide.
- **Portfolio = DoED promoter of record.** A holding company such as BPCL shows only the projects licensed in its own name,
  not its subsidiaries' plants, so its MW, cost/MW and outlook are understated.
- **Effective tax rate uses one period** (latest full quarterly report); a loss-making period reads "Unknown".
- ~10% of projects have no coordinates and ~10% no resolved district (DoED rows with placeholder values).
- 35 non-operational projects hold expired licences (see `analyze licences`); they are kept, not removed.
- Three near-duplicate pairs remain flagged by `validate-data` for human judgement.
- Browser rendering of the dashboard has been syntax-checked and its API exercised, but was not visually
  inspected in this environment.
