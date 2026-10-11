# Graph Report - Nepal Hydropwer Projects  (2026-10-04)

## Corpus Check
- 68 files · ~51,736 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1530 nodes · 4019 edges · 65 communities (54 shown, 8 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 317 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Chart.js (vendor) 0
- Chart.js (vendor) 1
- Chart.js (vendor) 2
- Technical Analytics
- Project Plan & Docker
- Chart.js (vendor) 5
- NEPSE Sync & Aliases
- CLI Reports & Discovery
- Data Cleaners
- Chart.js (vendor) 9
- CLI Entry Point
- Models & Data Loading
- Chart.js (vendor) 12
- Flask Dashboard API
- Chart.js (vendor) 14
- Company Profiles
- Chart.js (vendor) 16
- Company Trends
- Chart.js (vendor) 18
- Chart.js (vendor) 19
- Company Reports Discovery
- ShareSansar Scraper
- DB Sync & Merge
- Analytics API Endpoints
- Leaflet (vendor)
- Dashboard Frontend JS
- CLI Data Loading
- Chart.js (vendor) 27
- Chart.js (vendor) 28
- Chart.js (vendor) 29
- Chart.js (vendor) 30
- Revenue Estimation
- Ownership Analytics
- Database Manager & Export
- Chart.js (vendor) 34
- Listed Company Scraping
- Chart.js (vendor) 36
- Unknowns Analytics
- Chart.js (vendor) 38
- Chart.js (vendor) 39
- Chart.js (vendor) 40
- Name Matcher
- Chart.js (vendor) 42
- Chart.js (vendor) 43
- Chart.js (vendor) 44
- Chart.js (vendor) 46
- Chart.js (vendor) 47
- Chart.js (vendor) 48
- Chart.js (vendor) 49
- Chart.js (vendor) 50
- Report PDF Extraction
- Chart.js (vendor) 52
- Chart.js (vendor) 53
- Chart.js (vendor) 54
- Chart.js (vendor) 55
- Chart.js (vendor) 56
- Refresh Pipeline Tests
- Data Validators
- Financial History
- Return Profile (IRR/NPV)
- Chart.js (vendor) 61
- Location Gap Closing

## God Nodes (most connected - your core abstractions)
1. `Project` - 66 edges
2. `an()` - 61 edges
3. `ns()` - 55 edges
4. `Company` - 53 edges
5. `create_app()` - 47 edges
6. `s()` - 42 edges
7. `o()` - 41 edges
8. `a()` - 38 edges
9. `n()` - 37 edges
10. `no` - 35 edges

## Surprising Connections (you probably didn't know these)
- `Known gaps` --semantically_similar_to--> `Known limitations`  [INFERRED] [semantically similar]
  PROJECT_SUMMARY.md → README.md
- `main.py CLI workflow (load-sample, sync, web, analyze, export)` --semantically_similar_to--> `CLI command reference`  [INFERRED] [semantically similar]
  QUICKSTART.md → README.md
- `Repository layout` --semantically_similar_to--> `Planned system architecture`  [INFERRED] [semantically similar]
  README.md → hydropower_project_plan.md
- `test_csv_import_projects_and_financials()` --uses--> `ProjectStatus`  [INFERRED]
  tests/test_pipeline.py → src/models.py
- `profile_db()` --uses--> `Company`  [INFERRED]
  tests/test_company.py → src/models.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **DoED + Wikipedia + NEPSE merge pipeline** — readme_doed, readme_wikipedia, readme_merolagani_nepse, readme_merge_rules [EXTRACTED 1.00]
- **PPA verification flow** — readme_rating_agencies, readme_fact_review, readme_ppa_rates, readme_company_profiles [EXTRACTED 1.00]
- **Python runtime dependency stack** — requirements_sqlalchemy, requirements_flask, requirements_openpyxl, requirements_beautifulsoup4 [INFERRED 0.75]
- **Pages extending base.html layout** — dashboards_templates_home, dashboards_templates_projects, dashboards_templates_companies, dashboards_templates_news, dashboards_templates_analytics, dashboards_templates_map [EXTRACTED 1.00]
- **Pages using Leaflet maps** — dashboards_templates_map, dashboards_templates_project, dashboards_templates_company, dashboards_templates_leaflet [EXTRACTED 1.00]

## Communities (65 total, 8 thin omitted)

### Community 0 - "Chart.js (vendor) 0"
Cohesion: 0.04
Nodes (28): at(), b(), beforeLayout(), cn(), destroy(), dn(), draw(), ga() (+20 more)

### Community 1 - "Chart.js (vendor) 1"
Cohesion: 0.05
Nodes (12): As(), beforeUpdate(), bn(), initialize(), labelColor(), labelPointStyle(), ns(), pn() (+4 more)

### Community 2 - "Chart.js (vendor) 2"
Cohesion: 0.10
Nodes (47): a(), aa(), ai(), ao(), ca(), da(), e(), et() (+39 more)

### Community 3 - "Technical Analytics"
Cohesion: 0.07
Nodes (54): capacity_by(), commissioning_timeline(), expiring_licences(), forecast_capacity(), pipeline_by_year(), DataFrame, date, Capacity, status, timeline and licence analytics. (+46 more)

### Community 4 - "Project Plan & Docker"
Cohesion: 0.06
Nodes (48): docker-compose hydropower service, ADMIN_TOKEN env var, Nepal Hydropower Database System Project Plan, Planned system architecture, Data points per project, Key data sources (DoED, NEA, SEBON, World Bank), Six implementation phases, Success metrics (+40 more)

### Community 5 - "Chart.js (vendor) 5"
Cohesion: 0.06
Nodes (17): Bt(), ce(), color(), de, Ft(), Gt(), he(), It() (+9 more)

### Community 6 - "NEPSE Sync & Aliases"
Cohesion: 0.07
Nodes (35): upsert_projects(), load_aliases(), Session, NEPSE-listed hydropower companies (symbol, name, listed shares) from…, User-confirmed links: CSV with columns `symbol,company_name`, where…, Mark matching companies as NEPSE-listed (creating any that don't exist yet).…, _squash(), sync_listed_companies() (+27 more)

### Community 7 - "CLI Reports & Discovery"
Cohesion: 0.10
Nodes (40): add_url_cmd(), _companies(), discover_cmd(), _each(), extract_cmd(), fetch_cmd(), financials_cmd(), guess_websites_cmd() (+32 more)

### Community 8 - "Data Cleaners"
Cohesion: 0.08
Nodes (40): bs_to_ad(), discriminators(), dms_to_decimal(), _fold(), name_tokens(), normalize_company_name(), normalize_project_name(), parse_capacity() (+32 more)

### Community 9 - "Chart.js (vendor) 9"
Cohesion: 0.08
Nodes (11): buildLookupTable(), En, Fo(), _generate(), getDecimalForValue(), _getTimestampsForTable(), init(), initOffsets() (+3 more)

### Community 10 - "CLI Entry Point"
Cohesion: 0.09
Nodes (40): Entry point: python main.py <command>, backup(), cli(), import_csv(), main(), argument, group, Command-line interface. Run via `python main.py <command>`. (+32 more)

### Community 11 - "Models & Data Loading"
Cohesion: 0.11
Nodes (29): datetime, DeclarativeBase, clean_river(), load_projects_df(), DataFrame, Session, Load the project table as one flat DataFrame for analysis., Khimti Khola' / 'khimti nadi' -> 'Khimti' so the same river groups together. (+21 more)

### Community 12 - "Chart.js (vendor) 12"
Cohesion: 0.07
Nodes (17): Be(), _calculateBarIndexPixels(), determineDataLimits(), getPixelForTick(), getPixelForValue(), _getRuler(), _getStackCount(), _getStackIndex() (+9 more)

### Community 13 - "Flask Dashboard API"
Cohesion: 0.09
Nodes (26): Flask, _clean(), create_app(), api_capacity(), api_companies(), api_company_profile(), api_company_profiles(), api_geojson() (+18 more)

### Community 14 - "Chart.js (vendor) 14"
Cohesion: 0.09
Nodes (7): Cs, fe(), ks(), nn(), os(), sn, xt

### Community 15 - "Company Profiles"
Cohesion: 0.11
Nodes (32): build_profile(), list_profiles(), _pick_fact(), Session, Company profiles: portfolio by stage, reported financials, and derived metrics…, One summary row per company for the Companies table., Tax-free or not, from reported numbers first (effective rate), with any report…, The most frequently reported value of a fact type (ties: latest fiscal year). (+24 more)

### Community 16 - "Chart.js (vendor) 16"
Cohesion: 0.12
Nodes (7): afterDraw(), afterEvent(), Ee(), Le(), Oi(), va, x()

### Community 17 - "Company Trends"
Cohesion: 0.11
Nodes (26): _latest(), Most recent period that has balance-sheet data (a full quarterly report, not…, build_trend(), standalone(), _fmt_npr(), _full_metrics(), _latest_analysis(), _pct() (+18 more)

### Community 18 - "Chart.js (vendor) 18"
Cohesion: 0.10
Nodes (27): average(), dataset(), ei(), getCenterPoint(), ho(), Hs, _i(), index() (+19 more)

### Community 19 - "Chart.js (vendor) 19"
Cohesion: 0.12
Nodes (3): an(), Mn(), reset()

### Community 20 - "Company Reports Discovery"
Cohesion: 0.11
Nodes (25): Response, bs_fiscal_year(), candidate_domains(), classify_link(), discover(), download(), find_website_by_search(), _get() (+17 more)

### Community 21 - "ShareSansar Scraper"
Cohesion: 0.12
Nodes (19): CompanyInfo, parse_company_info(), parse_number(), parse_profit_headline(), parse_quarterly_html(), ShareSansar: structured quarterly financials, company details and announcement…, A company's own mail domain is a good hint for its website; free/ISP mail…, Stage 1: company details (website / email / ShareSansar id). One request. (+11 more)

### Community 22 - "DB Sync & Merge"
Cohesion: 0.20
Nodes (23): _cap_close(), _material(), _next_id(), Session, Merge scraped records (DoED, Wikipedia) into the database without creating…, sync_records(), SyncResult, ProjectStatus (+15 more)

### Community 23 - "Analytics API Endpoints"
Cohesion: 0.11
Nodes (17): /api/analytics/capacity, /api/analytics/licences, /api/analytics/unknowns, /api/companies/profiles, /api/companies/<id>/profile, /api/export/excel, /api/geojson, /api/news (+9 more)

### Community 24 - "Leaflet (vendor)"
Cohesion: 0.09
Nodes (4): a(), Ci(), l(), x()

### Community 25 - "Dashboard Frontend JS"
Cohesion: 0.16
Nodes (20): applyChartTheme(), baseChartOptions(), CHART_SERIES(), companyLink(), cssVar(), el(), fmt(), icon() (+12 more)

### Community 26 - "CLI Data Loading"
Cohesion: 0.12
Nodes (22): export(), init(), load_sample(), load_seed(), command, option, pass_context, query() (+14 more)

### Community 27 - "Chart.js (vendor) 27"
Cohesion: 0.18
Nodes (6): afterUpdate(), ba, gs(), ki(), oa(), Si()

### Community 29 - "Chart.js (vendor) 29"
Cohesion: 0.19
Nodes (6): _calculateBarValuePixels(), getBasePixel(), getLabelAndValue(), getLabelForValue(), resolveDataElementOptions(), updateElements()

### Community 31 - "Revenue Estimation"
Cohesion: 0.14
Nodes (19): mw(), blended_tariff(), estimate_energy_gwh(), estimate_revenue_df(), ESTIMATED annual energy and revenue = MW x 8760h x capacity factor x blended…, _assumptions_sheet(), build_workbook(), _full_frame() (+11 more)

### Community 32 - "Ownership Analytics"
Cohesion: 0.16
Nodes (18): company_projects(), developer_ranking(), listed_without_projects(), nepse_exposure(), project_shareholders(), DataFrame, Session, Developer rankings and NEPSE-listed company exposure. (+10 more)

### Community 33 - "Database Manager & Export"
Cohesion: 0.13
Nodes (9): api_export(), DatabaseManager, Session, Idempotently load districts, rivers and reference companies., Lightweight migration: ADD COLUMN for model columns an older database file…, One-time migration: project_updates.project_id used to be required so a…, db(), fixture (+1 more)

### Community 34 - "Chart.js (vendor) 34"
Cohesion: 0.12
Nodes (5): bo, getValueForPixel(), H(), mo(), xo

### Community 35 - "Listed Company Scraping"
Cohesion: 0.18
Nodes (12): Series, Scrape DoED / Wikipedia / NEPSE listing and merge into the database…, sync(), scrape_listed_hydropower(), DoEDScraper, fetch_project_bank(), _get(), DataFrame (+4 more)

### Community 36 - "Chart.js (vendor) 36"
Cohesion: 0.14
Nodes (9): ct(), dt(), fs(), ge(), ms(), ps(), vs(), ws (+1 more)

### Community 37 - "Unknowns Analytics"
Cohesion: 0.25
Nodes (15): DataFrame, What 'Unknown' means in the analytics: which projects lack each attribute, how…, The projects behind an 'Unknown' bar, biggest first, with what IS known about…, summary(), unknown_projects(), ProjectType, _load(), _run() (+7 more)

### Community 38 - "Chart.js (vendor) 38"
Cohesion: 0.14
Nodes (12): beforeDatasetDraw(), beforeDatasetsDraw(), beforeDraw(), es(), getRange(), hi(), is(), na() (+4 more)

### Community 39 - "Chart.js (vendor) 39"
Cohesion: 0.20
Nodes (9): buildTicks(), Fn(), go(), parse(), parseArrayData(), parseObjectData(), parsePrimitiveData(), po() (+1 more)

### Community 40 - "Chart.js (vendor) 40"
Cohesion: 0.20
Nodes (3): Do(), eo(), Oe()

### Community 41 - "Name Matcher"
Cohesion: 0.25
Nodes (6): _Entry, _Matcher, Numeric licence numbers are unique per licence type ('1_gtd'-style shared…, (compatible, both_known_and_equal), Two different numeric licence numbers of the same type belong to two different…, Project

### Community 42 - "Chart.js (vendor) 42"
Cohesion: 0.21
Nodes (3): afterDatasetsUpdate(), generateLabels(), onClick()

### Community 47 - "Chart.js (vendor) 47"
Cohesion: 0.24
Nodes (12): bi(), e(), hi(), m(), Pi(), Qe(), Ti(), u() (+4 more)

### Community 48 - "Chart.js (vendor) 48"
Cohesion: 0.21
Nodes (12): F(), G(), j(), k(), me(), ne(), e(), Oe() (+4 more)

### Community 49 - "Chart.js (vendor) 49"
Cohesion: 0.27
Nodes (4): Ae(), Bi(), Ci(), Fi()

### Community 50 - "Chart.js (vendor) 50"
Cohesion: 0.20
Nodes (11): Ae(), be(), Ie(), Jt(), ke(), Le(), O(), Qt() (+3 more)

### Community 51 - "Report PDF Extraction"
Cohesion: 0.24
Nodes (10): Match, dedupe(), extract_from_pdf(), extract_from_text(), Path, Pull candidate facts (project cost, PPA rate, tax holiday) out of report PDFs,…, Read a PDF page by page (pypdf) and collect fact candidates. Scanned/image-only…, Keep the first few distinct facts of each type (annual reports repeat the same… (+2 more)

### Community 52 - "Chart.js (vendor) 52"
Cohesion: 0.22
Nodes (10): De(), ei(), ii(), Je(), ni(), oi(), p(), pe() (+2 more)

### Community 53 - "Chart.js (vendor) 53"
Cohesion: 0.28
Nodes (4): addBox(), configure(), start(), vn()

### Community 55 - "Chart.js (vendor) 55"
Cohesion: 0.22
Nodes (5): ea(), ha, la(), sa(), ua()

### Community 56 - "Chart.js (vendor) 56"
Cohesion: 0.28
Nodes (9): at(), d(), ht(), i(), Li(), Mi(), v(), W() (+1 more)

### Community 57 - "Refresh Pipeline Tests"
Cohesion: 0.22
Nodes (4): calls(), fixture, Replace every stage's work with a recorder so no network or database is touched., test_a_failing_source_does_not_stop_later_stages_and_exit_code_flags_it()

### Community 58 - "Data Validators"
Cohesion: 0.36
Nodes (7): Issue, Session, Data-quality checks: per-record validation before insert, and a whole-database…, Validate a single record dict. Returns a list of error strings (empty = valid)., validate_database(), validate_project_data(), test_validators()

### Community 59 - "Financial History"
Cohesion: 0.38
Nodes (7): financial_history(), portfolio_financials(), DataFrame, Session, Reported annual financials for one project, with capacity factor derived when…, Reported generation and revenue summed by fiscal year across all projects with…, test_financial_history_derives_capacity_factor()

### Community 60 - "Return Profile (IRR/NPV)"
Cohesion: 0.40
Nodes (5): irr(), project_return_profile(), Simple unlevered profile: payback and IRR for a flat annual net cash flow.…, Internal rate of return by bisection. cashflows[0] is the (negative) initial…, test_irr_known_values()

## Knowledge Gaps
- **28 isolated node(s):** `STATUS_VARS`, `STATUS_COLORS`, `THEME_TOKENS`, `Company financial trends (company_trends.py)`, `Location and province gap closing` (+23 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 373 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `an()` connect `Chart.js (vendor) 19` to `Chart.js (vendor) 0`, `Chart.js (vendor) 2`, `Chart.js (vendor) 5`, `Chart.js (vendor) 38`, `Chart.js (vendor) 42`, `Chart.js (vendor) 44`, `Chart.js (vendor) 12`, `Chart.js (vendor) 49`, `Chart.js (vendor) 18`, `Chart.js (vendor) 53`, `Chart.js (vendor) 54`, `Chart.js (vendor) 61`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Why does `Project` connect `Name Matcher` to `Ownership Analytics`, `Technical Analytics`, `Data Validators`, `Unknowns Analytics`, `NEPSE Sync & Aliases`, `CLI Reports & Discovery`, `Data Cleaners`, `CLI Entry Point`, `Models & Data Loading`, `Flask Dashboard API`, `Company Profiles`, `DB Sync & Merge`, `CLI Data Loading`, `Financial History`, `Revenue Estimation`?**
  _High betweenness centrality (0.030) - this node is a cross-community bridge._
- **Why does `Company` connect `NEPSE Sync & Aliases` to `Ownership Analytics`, `Database Manager & Export`, `CLI Reports & Discovery`, `CLI Entry Point`, `Models & Data Loading`, `Flask Dashboard API`, `Company Profiles`, `Company Trends`, `Company Reports Discovery`, `ShareSansar Scraper`, `DB Sync & Merge`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Are the 38 inferred relationships involving `Project` (e.g. with `build_profile()` and `load_projects_df()`) actually correct?**
  _`Project` has 38 INFERRED edges - model-reasoned connections that need verification._
- **Are the 26 inferred relationships involving `Company` (e.g. with `build_profile()` and `list_profiles()`) actually correct?**
  _`Company` has 26 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `create_app()` (e.g. with `DatabaseManager` and `Company`) actually correct?**
  _`create_app()` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `STATUS_VARS`, `STATUS_COLORS`, `THEME_TOKENS` to the rest of the system?**
  _28 weakly-connected nodes found - possible documentation gaps or missing edges._