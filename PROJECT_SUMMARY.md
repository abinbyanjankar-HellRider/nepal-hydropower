# Project Summary: Build Status

All six phases of the plan are implemented and tested. This file records what was built, what was verified, and
what is still open.

## Phase status

| Phase | Delivered | Verified by |
|---|---|---|
| 1. Foundation | SQLAlchemy models (9 tables), config, DB manager, reference data (77 districts, rivers), CLI, seed set | Tests; live DB build |
| 2. Data collection | DoED scraper (6 register pages), Wikipedia scraper, NEPSE-listing scraper, RSS news collector, cleaners (BS→AD dates, DMS→decimal coordinates, district resolution, name folding), cross-source merge engine, validators | Tests; 3 consecutive live syncs (2nd and 3rd create 0 rows) |
| 3. Analytics | Capacity/timeline/pipeline/licence analytics, developer ranking, NEPSE exposure, revenue *estimates*, IRR/payback helpers, forecast scenario, `analyze` CLI | Tests; IRR checked against known values |
| 4. Dashboard | Flask app, 6 pages, JSON API, Leaflet map, Chart.js charts, token-gated admin endpoint | API tests; JS syntax-checked with Node; all endpoints exercised live |
| 5. Excel | 7 workbook types; Summary sheet uses live COUNTIF/SUMIF formulas, Financial sheet is linked to an Assumptions sheet | Formulas recalculated with the `formulas` engine and matched to the database (counts and MW exact; revenue within rounding) |
| 6. Enhancement | `refresh` one-shot command, `backup`, capacity forecast, Dockerfile/compose, `.env.example` | Forecast tested; `refresh` run end to end. **Docker not built or run** |

## Current data
816 projects: 223 operational (4,010 MW), 221 under construction (9,487 MW), 75 licensed (3,946 MW),
297 planned (27,960 MW). 754 rows backed by DoED, 62 Wikipedia-only, no seed rows left (the last one, an unsourced 'Upper Hewa Khola 14.9 MW / Arun Valley', was removed: DoED lists Upper Hewa as 8.5 MW by another company and 14.9 MW is Hewa Khola-A in Panchthar). All 111 NEPSE-listed hydropower companies
loaded; 110 are promoter of record on at least one project (only TVCL is not).

## Issues found and fixed during the build
- Hand-typed NEPSE symbols were wrong (e.g. `RHPL` is Rasuwagadhi Hydropower, not Ridi). Removed; symbols now
  come from the scraped listing.
- Wikipedia "Total" rows were imported as projects; a 2,000 MW cap wrongly rejected Sapta Koshi/Pancheshwar.
- Spelling variants (Trisuli/Trishuli, Budi/Budhi, …) created duplicates; fixed with name folding while keeping
  Upper/Lower and numbered variants apart.
- A too-loose matching rule merged a seed plant into an unrelated licensed project and broke idempotency;
  removed, licence-number matching added, regression test written.
- Fuzzy NEPSE company matching linked `SMHL` (Super Madi) to the Super Mai company and inflated coverage from 68 to
  88 listed companies with projects; replaced with strict matching, an alias file and a `--suggest` review aid.
- A news item about "Upper Trishuli-1" was also attached to the unrelated "Trishuli" plant; now only the most
  specific project matches.

## Merge fix (Seti Khola, Rahughat, Chulepu)
Looking into the stale Seti Khola status turned up two wrongly fused pairs. DoED's operational list has a 25 MW "Seti Khola HPP"
(Vision Lumbini Urja, licence 304) and a 37 MW "Rahughat Mangale" (Tundi Power, licence 175); the matcher had merged them into the
22 MW Seti Khola HEP (licence 334) and the 40 MW Rahughat (licence 49), which made both wrongly "operational" with the wrong
commissioning date. The matcher now never merges two projects whose numeric licence numbers of the same type differ, and it folds
"Setikhola" to "Seti Khola" (which had also caused two Wikipedia-only duplicates). Corrections, each with an `inference_note`:
Seti Khola HEP stays Operational on news reports of first generation in July 2026 (date cleared, year 2026); Rahughat 40 MW is
Under Construction; the two split-out plants are now separate operational rows; two duplicates were removed. Regression tests added.

### Second merge fix: Wikipedia duplicates
A scan of the "operational" rows without DoED backing found Wikipedia rows that duplicated DoED plants because Wikipedia's capacity
differs (Middle vs Madhya Marsyangdi 70 MW, Bagmati Nadi 22 vs 32 MW, Thapa Khola, Super Mai Khola Cascade) and similar planned or
under-construction rows (Bhotekoshi 5, Chujung Khola, Sabha Khola C, Upper Tamor A, Seti Nadi-3, Middle Chameliya). The matcher now
merges a Wikipedia row into a DoED project when the folded name is identical and the district is the same (or the Wikipedia row has
no district and the name is unique), up to a 40% capacity gap; DoED's capacity is kept. "Madhya" now folds to "Middle". Ten stale
duplicate rows were removed (107 MW of operational, 116 MW of under-construction and 172 MW of planned capacity had been counted twice). Three licensed
projects moved to Under Construction on Wikipedia's evidence. Mandu's DoED capacity (32 MW) disagrees with ICRA and Wikipedia (22 MW);
DoED's value is kept and the conflict is noted on the record.

### Third merge fix: spelling variants and locations
Wikipedia's "Chhomoron Khola Small HEP" (4.89 MW) and "Paara Malun PRoP HEP" (8.53 MW) were duplicates of DoED's "Chhomron Khola Small HEP"
(4.894 MW, Kaski) and "Paara Molung PRoR" (8.53 MW, Okhaldhunga). The matcher now also merges a Wikipedia row when the capacity is within 1% and
the folded names are at least 80% similar (same discriminators). The location resolver's project-bank match is now space-insensitive, which
located "Mathillo Chhum Chhum Gad" (Darchula). Six small projects (29.5 MW: Nyam Nyam, Jhyaku Khola, Phalaku Khila, Chisang Khola-A,
Istul Khola, Tawa Khola) still have no location: web searches and a fuzzy comparison with every DoED table found no reliable match.

### Company links (alias file)
`data/company_aliases.csv` (`symbol,company_name`) now holds 10 confirmed links between a NEPSE symbol and the promoter name DoED uses:
KAHL, MKJC, MAKAR, MEHL, MCHL, DORDI, CHL, IHL, SMJC, KHPL. Each was accepted only when a rating report about that listed company
states a plant whose capacity matches a DoED project exactly and whose name and district appear in the same report, and the DoED developer
name is the same company spelled differently. The NEPSE-created duplicate company rows were merged into the DoED company (facts,
financials and reports moved, duplicate deleted), so 79 of 111 listed companies now have a project (was 70). Candidates that failed the
test and were left unlinked: United Modi, United IDI Mardi, Panchakanya Mai (its Upper Mai-C plant belongs to a different developer),
Himal Dolakha (name variant only, no report evidence). The name-similarity output of `analyze nepse --suggest` is too noisy to use alone.

### Company links, second round
27 aliases now sit in `data/company_aliases.csv` (was 10) and 96 of 111 listed companies have a project (was 79). New links were accepted when
a report about the listed company states the plant (Swet-Ganga: Tallo Likhu 28.1 MW; Joshi: Upper Puwa-I 3 MW, Illam; Suryakunda: Tadi River,
Nuwakot, 11 MW; Shiva Shree: Upper Chaku A 22.2 MW; Madhya Bhotekoshi, Ankhu Khola-1, Appolo, Sikles, Dibyashwori, Ru Ru), or when the legal
name equals DoED's promoter name apart from a spelling, plural or Pvt/Public difference (Barahi, Bindhyabasini, Snow River(s), Himalaya(n) Power
Partner, Himal Dolakha/Dolkha, Sanjen, Mathillo Mailung). Shiva Shree's report also gave a verified PPA (base 4 / 7, so 44 of 111 now).
New `data/company_name_variants.csv` (`variant,canonical`) keeps one DoED promoter spelled two ways as one company (Sanjen, Mathillo Mailung).
Still unlinked (15): BARUN (Barun Hydropower Co. Ltd.); CKHL (Chirkhwa Hydropower Limited); GLH (Greenlife Hydropower Limited); KPCL (Kalika power Company Ltd); MHCL (Molung Hydropower Company Limited); NGPL (Ngadi Group Power Ltd.); NHDL (Nepal Hydro Developers Ltd.); PMHPL (Panchakanya Mai Hydropower Ltd); RFPL (River Falls Power Limited); RHGCL (Rapti Hydro and General Construction Limited); SMHL (Super Madi Hydropower Limited); SPL (Shuvam Power Company Limited); TVCL (Trishuli Jal Vidhyut Company Limited); UMHL (United Modi Hydropower Pvt. Ltd.); UMRH (United IDI Mardi RB Hydropower Limited.). Each needs a report or website that names the plant; Trishuli Jal Vidhyut's Upper Trishuli 3B (37 MW) is listed under NEA in DoED, so it cannot be linked through an alias.

### Company links, third round: nearly complete
41 aliases now sit in `data/company_aliases.csv` (was 27) and 110 of 111 listed companies have a project (was 96). Each of the 14
remaining companies had a working website (a wrong website was the reason none had linked before): the site names a plant, and that
plant matches a DoED project by capacity, name and district. Two also needed a spelling-variant read (Baneswar/Baneshwor, Suiri/Siuri).
One (NHDL) had a wrong website recorded (nepaldevelopers.com, an unrelated web-design studio); cleared, no replacement found.
KPCL's own site (kalikagroup.com) has an expired TLS certificate, so the link came from a web search instead.
Only **TVCL** (Trishuli Jal Vidhyut) remains unlinked: its own project, Upper Trishuli 3B (37 MW), is registered to Nepal Electricity
Authority in DoED, not to TVCL, so no alias can fix it — DoED's promoter record itself would need to change.

## Known gaps (see README for detail)
- No public bulk source for financials, shareholders or technical details: tables are empty, importers exist.
- Wikipedia-only rows can contain errors; DoED and Wikipedia disagree on some commissioning years
  (`data/processed/sync_conflicts.txt`).
- ~10% of projects lack coordinates or a district; 35 non-operational projects hold expired licences.
- Dashboard was not visually inspected (no browser available); PostgreSQL and Docker are untested.
- Not built from the original plan: APScheduler (use the OS scheduler with `refresh`), GraphQL, mobile app.

## Original plan
The design and phase breakdown remain in `hydropower_project_plan.md`.

## Company profiles and reports (added later)
- New tables `company_financials`, `company_reports`, `company_facts` (each fact keeps its report, page and snippet),
  plus website/email columns on companies and an automatic ADD COLUMN migration so existing databases keep their data.
- Pipeline `reports run`: company info -> find PDFs on company sites -> **all annual reports** -> **all quarterly reports**
  -> ShareSansar structured quarterly financials -> fact extraction. Also `guess-websites`, `set-website`, `add-url`,
  `status`, and `import-csv --kind facts` for verified values.
- Profile pages `/companies/<id>` and a Companies table with cost/MW, PPA, tax status, loans, finance cost, debt/equity and
  an outlook scenario; every derived number states its basis.
- Result: financials for 111 companies; websites for 86 of 111; 228 annual + 297 quarterly PDFs from 46 / 37 companies; extraction yield low.

### Problems found while building it
- Merolagani and ShareSansar host no report PDFs (only structured figures / image viewers), so PDFs come from company sites.
- The first cost-per-MW proxy used book PP&E and showed NPR 0.7-10 m per MW because plants are booked under "Other
  assets"; replaced with a capital-employed proxy hidden outside NPR 60-700 m/MW.
- An extracted "PPA rate" (NPR 10.80) was a retail tariff; auto-extracted values no longer become headline numbers.
- Website discovery: guessing domains produced two wrong matches (`api.com.np` is an auto-parts firm; `sayapatri.com` is a
  parked domain), so verification now needs the company's name AND hydropower words on a real page, refuses parked/thin
  pages, and is stricter for one-word names. DuckDuckGo started serving a human-verification challenge after a burst of
  automated searches; that is not bypassed, and the remaining larger companies were searched with the web search tool instead.

## Financial trends (added later)
- `company_trends.py` builds, per company: quarter-by-quarter profit for the last five fiscal years (standalone and
  cumulative, with change against the same quarter last year), a latest-report analysis, and a five-year table. Profiles and
  the Companies table (new "Latest profit YoY" column) use it; 56 of 111 companies have a computable latest YoY.
- Verified: Chilime's annual profits (720 / 680 / 653 / 694 / 768 m) and Q1/Q4 figures match ShareSansar's published numbers.
- Limit found: multi-year ratios cannot be sourced for free (see README); missing values are left blank, and a CSV loader
  (`--kind company-financials`) backfills them from annual reports.

## Remaining gaps closed as far as the evidence allows
- Unknown province: 77 projects (15,020 MW) -> 39 (3,443 MW) -> **18 (542 MW)**. The last step used DoED's project-bank
  lists (government-studied, under-study and generation-licence applications): 21 projects matched by name and capacity.
  The 18 left have no coordinates, district or name match anywhere; they are left Unknown, not guessed.
- Company websites: 61 -> **96 of 111**. Web search plus a page-content check found 35 more; 15 companies (single-project
  firms, mostly Gmail-only or Facebook-only) have no findable site. One candidate (Terhathum) now serves a registrar
  placeholder and was rejected.
- Reports: 294 annual (53 companies) and 362 quarterly (44 companies). Extraction yield stays low (see README).

## Key-number review
- Read all 19 cost / PPA candidates against their source text. Verified 8 (linked to Upper Tamakoshi, Kabeli-A and Mai Beni),
  rejected 11 (retail tariff, solar project cost, superseded estimates). Cost per MW now comes from reported costs for those
  companies (108 / 200 / 210 m NPR per MW) instead of the capital proxy. `verify-fact` / `reject-fact` record each decision.
- No PPA rate could be verified: the only candidate was a retail tariff.

## Pending list, worked through (21 Sep 2026)
- Provinces: 18 projects (542 MW) -> **10 (61.5 MW)**. Eight confirmed from named sources and recorded in
  `data/location_overrides.csv` (Naumure, Dudhkoshi Storage, Sunkoshi Marin, Budhiganga, Chauwa Khola, Seti Khola, Nimrung
  Khola, Lower Tara Khola). Ten small projects have no findable location and stay Unknown. Seti Khola is now commissioned
  (July 2026 reports) while the database still lists it under construction; statuses come from DoED/Wikipedia and were not edited.
- Websites: 96 -> **104 of 111**. Manakamana (account suspended) and Terhathum (registrar placeholder) rejected. Six sites are
  tagged "not machine-verified". Later, Himalayan Hydro Power (hhpl.com.np) and Universal Power (universalpowercompany.com.np) were found
  by web search and checked against their projects (Namarjun Madi 12 MW, Tallo Khare Khola 11 MW): now **106 of 111**. HHL gave 2 annual
  reports; UPCL's report links are all labelled "View Notice", so its 2 quarterly PDFs were added by hand with `reports add-url`.
  5 companies remain without a site: MKHC, MEHL, RAWA, TPC, TPKHL (web search found none).
- PPA: Upper Tamakoshi's seasonal PPA verified from a primary source; NEA's standard rates confirmed. Profiles now support wet/dry rates.

## PPA rates from rating reports
- ICRA Nepal / CARE Ratings Nepal / Infomerics reports state a company's PPA tariff in plain text, unlike annual reports.
  Read 16 such reports; 13 companies verified (all NEA's standard 4.80 wet / 8.40 dry with 3% escalation on base, one mixed).
  Total with a verified PPA: **14 of 111** (with Upper Tamakoshi).
- Bulk discovery is not possible: ICRA lists only its latest 16 releases and CARE's finder is a dynamic form, so more
  companies need one search each. Pattern for adding them: fetch the report, read the tariff sentence, add rows to a CSV
  and `import-csv --kind facts`.
- Second batch: 14 more companies read from rating reports (the search summaries were checked against the PDFs, since one earlier
  summary was wrong). Verified PPA total is now **28 of 111**. Three are not the standard 4.80 / 8.40: Upper Tamakoshi
  (3.63 / 6.96), Mailung Khola (present 3.72 / 5.27) and Sanima Mai (present 5.08 / 8.89). Panchakanya Mai was skipped because its
  report gives two different tariffs; Chilime's own PPA was not found.
- Third batch: 7 more (Sanigad Hydro, Kalanga Hydro, Panchthar Power, Arun Kabeli, Mountain Hydro Nepal, Bungal Hydro at the standard
  rates; Himalayan Hydropower at a 4 / 7 base tariff from its 2009 PPA). Verified PPA total is now **35 of 111**. Skipped on purpose:
  Api Power (its reports give 4 / 7 for the operating Naugad Gad plant but 4.80 / 8.40 for Upper Chameliya, so no single company rate),
  Mountain Energy Nepal (mixed 3.90-8.40 and 5.40), Green Ventures (only a "4.80 to 8.40" range, wet/dry not stated), and Mandu and
  Balephi (reports give no tariff figures). Kalanga Hydro Ltd (KAHL) has no linked project because DoED lists the operating plant under
  "Kalanga Hydropower P Ltd", a separate company record. Later confirmed from ICRA's report (15.33 MW Kalanga Gad, Bajhang) and linked.
- Fourth batch: 5 more at the standard 4.80 / 8.40 (Nyadi, Modi Energy, Ridge Line Energy, Taksar Pikhuwa Khola, Him Star Urja; Him Star's
  PPA uses a 6-month wet / 6-month dry split). Verified PPA total is now **40 of 111**. Skipped: Singati Hydro and Ridi Power (reports
  give only a "4.80 to 8.40" / "3.00 to 8.40" range, not wet vs dry), Himalaya Urja (two plants on 4/7 and 4.8/8.4) and Arun Valley (no
  tariff figures in the report).
- Fifth batch: 3 more at 4.80 / 8.40 (Maya Khola from an Infomerics release, Mandu from ICRA 2022, Balephi from ICRA's 2019 IPO grading).
  Verified PPA total is now **43 of 111**. Mandu's PPA covers the 22 MW plant; a further 10 MW unit signed a separate PPA in April 2025.
  Skipped: Butwal Power (its ICRA report mixes a 6-13 flat tariff with 4.8 / 8.4 for another plant), Chilime (no rating report found;
  the ICRA link returned 404), and the search summaries for Singati, Green Ventures and Ridi (only ranges). The Mandu capacity
  conflict is now explained: DoED's 32 MW is the licensed capacity including a 10 MW unit under construction (22 MW operating).
