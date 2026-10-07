# QA review of the legacy modules (read-only)

Scope: `src/analytics/{financials,technical,ownership,company_trends,company_profiles}.py`, `src/processors/{cleaners,validators,location_infer}.py`, `src/collectors/{sync,manual_import}.py`, `src/exports/excel_builder.py`.
Every finding below was reproduced, either against `data/projects.db` (opened `mode=ro`) and the raw DoED CSVs in `data/raw/`, or with an in-memory SQLite / in-memory objects. No project file, git state or database was modified.

Totals: **1 Critical, 11 Important, 8 Minor.**

---

## Critical

### C1. A DoED survey-licence row overwrites a different project that already holds a generation licence (data corruption already in the DB)
- **Where:** `src/collectors/sync.py:107` (`claimed` is tracked per stage) together with `sync.py:200-207` (an authoritative stage overwrites every field).
- **Defect:** Records are processed in the order powerplants → generation → survey. A project claimed in the `generation` stage is still free in the `survey` stage. The licence-conflict guard (`_licence_conflict`) only compares licences of the *same type*, so a survey row with a merely similar name (stage 1 allows 15% capacity difference; stage 2 allows token containment and 8%) matches it. The row then overwrites the name, capacity, licence type and number, licence dates, coordinates and developer.
- **Reproduction (real DB, two generation-licensed projects are gone):**
  - The raw `doed_clhydromorethan1_20260922.csv` has `Sabha Khola A, 10.4 MW, Lic 128, Deepsabha Hydropower`. The raw `doed_hydromorethan1` has `Sabha A Hydropower Project, 9.0 MW, Lic 1282, Standard H. Energy`. In the DB, `HP_243` = `Sabha A Hydropower Project, 9.0, UNDER_CONSTRUCTION, Survey, 1282, developer 496 (Standard H. Energy)`, with data_source `DoED:clhydromorethan1; DoED:hydromorethan1; ...`. No project with Generation licence 128 exists.
  - Likewise, `Sani Bheri HEP, 44.52 MW, Lic 389, Expert Hydro` (generation) became `HP_367 Rukum Sani Bheri Hydropower Project, 45.0, Survey 1457, O.S.R. Hydro`. Stage 2 matched them because {sani, beri} ⊂ {rukum, sani, beri}. No project with Generation licence 389 exists.
  - An in-memory `sync_records` run with exactly those four rows gives `created=2, updated=2`, and both survivors carry the survey licence and the survey promoter. A later sync of the generation row alone renames `HP_001` back to `Sabha Khola A`, so the record flips on every full sync.
- **Fix:** For authoritative stages, refuse a match whose existing project came from a higher stage unless the licence number or promoter agrees (or never let a lower-stage row overwrite fields set by a higher stage). Then re-scrape and split HP_243 and HP_367.

---

## Important

### I1. Same name and capacity merges projects across districts, and a missing capacity matches any same-named project
- **Where:** `sync.py:125-128` and `sync.py:47-50` (`_cap_close` returns True when either side is None).
- **Defect:** Stage 1 accepts an identical folded name within 3% capacity *ignoring district*. With a None capacity it accepts any same-named project, picking the one closest to 0 MW. A DoED row then overwrites district, developer and name.
- **Reproduction (in-memory `_Matcher`):** existing `Seti Khola 1.5 MW (Kaski)` and `Seti Khola HEP 22 MW (Tanahun)`.
  - Record `{"Seti Khola HPP", capacity None, Tanahun}` → matches HP_1, the 1.5 MW plant in Kaski.
  - Record `{"Seti Khola", 1.5, Tanahun}` → also HP_1, a cross-district merge.
  - The real DB has 5 different projects that fold to `seti`. DoED rows with None capacity do not occur today (Wikipedia skips them), so the None-capacity half is latent. The cross-district half is live.
- **Fix:** Require a non-None capacity on both sides for name matches, and require that the districts agree whenever both are known (or that the licence agrees).

### I2. A re-sync silently overwrites hand-corrected values and resets their provenance
- **Where:** `sync.py:206-211`.
- **Defect:** An authoritative stage `setattr`s every non-None field and sets `data_reliability = "doed"`, even on rows that `upsert_project` or `location_overrides` marked `manual` or `csv-import`. Any change within 10% raises no conflict at all.
- **Reproduction (in-memory):** set HP_001 to `capacity_mw=10.0, data_reliability='manual'`, then sync the DoED row with 10.4 → `capacity 10.4, data_reliability 'doed'`, and `conflicts` is empty.
- **Fix:** Skip fields on rows whose `data_reliability` is `manual`/`csv-import`/`verified` (log them as conflicts instead), and keep the stronger reliability tag.

### I3. Name folding makes Ka == Kha (and 11 == 1, 100 == 10)
- **Where:** `src/processors/cleaners.py:50-51` (`_fold` strips h after k/g, then collapses repeated characters, digits included).
- **Defect:** The Nepali series letters Ka/Kha (क/ख) and Ga/Gha, which are the A/B discriminators, fold to the same token. Repeated digits also collapse.
- **Reproduction:** `name_tokens("Budhi Gandaki Kha") == name_tokens("Budhi Gandaki Ka") == ['budi','gandaki','ka']`. `name_tokens("Khimti 11") == name_tokens("Khimti 1")`. `name_tokens("Project 100 MW")` → `['10','mw']`. With an existing `Budhi Gandaki Ka 130 MW`, the in-memory matcher maps a `generation` record `Budhi Gandaki Kha 130 MW` onto it. In the DB the real pair (HP_402 260 MW / HP_403 130 MW) stays apart only because their capacities differ.
- **Fix:** Fold only alphabetic runs and treat `ka/kha/ga/gha` as distinct discriminator tokens before `_fold` (e.g. map them to `ka`, `kha` placeholders that `_fold` skips).

### I4. `normalize_company_name` cuts real company names at "and"/"&"
- **Where:** `cleaners.py:90`.
- **Defect:** The split meant for "Company A and Company B" lists also cuts single names that contain "and"/"&". Splitting on `;` alone leaves address text in.
- **Reproduction (raw DoED promoters vs DB):** 19 promoters are affected, e.g.
  - `Nepal Water & Energy Development Co. P. Ltd` → company 236 `"Nepal Water"`, the developer of Upper Trishuli-1 (216 MW).
  - `Research & Development Group` → `"Research"`.
  - `KCs Hotel and Multiple Industries` → `"KCs Hotel"`.
  - `United Modi Hydropower Pvt. Ltd., 1st Floor Heritage Plaza 2; ...` keeps the address.

  Two distinct promoters such as "Himal Hydro and General Construction" and "Himal Hydro & X" would also collapse into one Company.
- **Fix:** Split only when both sides look like company names (each ends in Ltd/Pvt/Company…), or stop splitting DoED promoter strings. Then repair the 19 names.

### I5. `company_key` does not fold spacing, so one company becomes two records
- **Where:** `cleaners.py:94-97`, used by `manual_import.get_or_create_company`.
- **Reproduction (real DB):**
  - `Nilgiri Khola Hydropower Company Pvt. Ltd.` (131, owns HP_160 38 MW) and `Nilgirikhola Hydropower Company Limited` (146, owns HP_175 71 MW).
  - `Omega Energy Developer` (282, Sunigad) and `Omega EnergyDeveloper` (168, Upper Sunigad).
  - `Bhote Koshi Power Company` (4) and `Bhotekoshi Power Company` (14).
  - `company_key('Sanima Mai Hydro Power Ltd') != company_key('Sanima Mai Hydropower Ltd')`.

  Each company profile and ranking shows only part of the portfolio.
- **Fix:** Build the key with spaces removed (and `hydro power`→`hydropower`, `khola` joined), then merge the existing duplicate companies.

### I6. A company's "latest" headline uses the latest *full* report even when newer profit figures exist
- **Where:** `src/analytics/company_trends.py:100` (`latest_key = max(full) if full else max(ytd)`).
- **Reproduction (real DB, IHL, company 169):** `build_trend(s,169)['latest']` → `FY 2082/83 Q3, net profit 11.87 m, yoy None`. The same data holds Q4 YTD **-31.41 m**, a loss. `list_profiles` publishes `latest_period` and `profit_yoy_pct` from this stale quarter.
- **Fix:** Take `latest_key = max(ytd)` and attach `full.get(latest_key)` (None if absent).

### I7. A negative tax provision (deferred-tax credit) is labelled "Tax-free (holiday)"
- **Where:** `src/analytics/company_profiles.py:37-41`.
- **Reproduction (real DB, latest Q4 2082/83):**
  - AKPL: net profit 79.7 m, tax −9.15 m → `Tax-free (holiday)`, −13.0%.
  - DORDI: `Tax-free (holiday)`, −6.8%.
  - CHL: `Tax-free (holiday)`, −1.7%.

  A credit is not evidence of a holiday. (The newer `income.py` fixed this pattern, but the Companies table still uses this function.)
- **Fix:** When `tax_provision_npr < 0`, return the label `"Tax credit (deferred tax)"` with the rate shown, and do not use the holiday label.

### I8. Debt-to-equity goes negative when equity is negative, which ranks the most indebted company as the least levered
- **Where:** `company_profiles.py:185` (and the same formula at `company_trends.py:44`).
- **Reproduction (real DB):** MCHL has paid-up 542.6 m, reserves −681.5 m and loans 1,171 m → `debt_to_equity = -8.43` in `list_profiles`. Sorted ascending, it appears as the least levered.
- **Fix:** Return None (and flag "negative equity") when `equity <= 0`.

### I9. A multi-plant company's headline PPA rate is whichever plant's fact was inserted first
- **Where:** `company_profiles.py:54-55` (`_pick_fact`), used at 146-157.
- **Defect:** `Counter.most_common(1)` breaks ties by insertion order, so the docstring's "ties: latest fiscal year" never applies. Wet and dry are also picked independently, and nothing is capacity-weighted.
- **Reproduction:**
  - Synthetic facts `[4.0 @2074/75, 4.8 @2081/82]` → returns 4.0, not the latest year.
  - Real DB: API (company 52) headlines `wet 4.00 / dry 7.00` (blend 4.90) from the 8.5 MW Nau Gad plant. Its other verified plant (Upper Naugad, 8 MW) is on 4.80/8.40, and its 40 MW plant has no fact.
  - Real DB: AHPC headlines 4.80/8.40 from the 9.94 MW plant, while the 3 MW Piluwa plant is on 3.90/5.25.
- **Fix:** Pick wet and dry from the same fact or project, break ties by the latest `fiscal_year`, and when several plants have rates either show a capacity-weighted blend or say "varies by plant".

### I10. Known duplicate projects exist in the DB and the validator misses them
- **Where:** `src/processors/validators.py:73-93`.
- **Reproduction (real DB):**
  - `HP_815 Myagdi Khola-B HEP 12.5 MW PLANNED` (Wikipedia) duplicates `HP_456 Maygdi Khola- B HEP 12.5 MW LICENSED` (DoED; same capacity, district and discriminator). The near-name ratio is `0.875 < 0.9`, so neither duplicate check fires.
  - `HP_824 Small Sabha Khola Small Hydropower Project 4.1 PLANNED` (Wikipedia) is very likely `HP_450 Super Sabha Khola Small Hydropower Project 4.1 LICENSED`.
  - In both cases the MW is counted twice in the pipeline and planned totals. `validate_database` reports only 2 duplicate suspects in the whole DB (Pikhuwa, Inkhu).
- **Fix:** Also flag pairs with the same district, capacity within 1% and ratio ≥ 0.8 (the same rule `sync` stage 3 uses), then merge the two pairs above.

### I11. `location_infer` copies exact coordinates from a different, same-named plant of very different size
- **Where:** `src/processors/location_infer.py:174-182` (step 3 ignores capacity; `normalize_project_name` drops "small", "cascade" and "upper"-less variants).
- **Reproduction (real DB, 7 rows noted "capacity differs"):**
  - `HP_824` (4.1 MW) got HP_085 Sabha Khola's (3.3 MW, operational) exact lat/lon 27.39611, 87.28306.
  - `HP_810 Upper Trisuli-I Cascade 24.6 MW` got the coordinates of `HP_270 Upper Trishuli-1 216 MW`.
  - `HP_779 Tinau Khola 3.44` got the coordinates of `Tinau 1.024 MW`.

  The map stacks pins on other plants.
- **Fix:** Copy only the district/province across a capacity gap, and copy coordinates only when capacity is within about 15% (as step 1b already requires).

---

## Minor

### M1. Excel formula injection through "=" values
- **Where:** `src/exports/excel_builder.py:67`.
- **Defect:** `ws.cell(i, j, v)` stores any string that starts with `=` as a formula.
- **Reproduction:** `_write_sheet(wb, 'Projects', DataFrame({'name': ['=HYPERLINK("http://evil","Click")']}))` → cell `data_type == 'f'`. `+`, `-` and `@` stay strings. No DB value starts with these characters today, but the names come from scraped Wikipedia/DoED text.
- **Fix:** Prefix `'` (or set `data_type='s'`) for strings that start with `=`, `+`, `-` or `@`.

### M2. `irr` returns None for IRRs above 100%
- **Where:** `src/analytics/financials.py:77,83-85`.
- **Defect:** The upper bracket is fixed at `hi=1.0`.
- **Reproduction:** `project_return_profile(100, 150)` → `{'simple_payback_years': 0.7, 'irr_pct': None}`.
- **Fix:** Expand `hi` until the NPV changes sign (or cap it and return ">100%").

### M3. Simple payback ignores the construction years
- **Where:** `financials.py:103`.
- **Reproduction:** `project_return_profile(100, 20, construction_years=3)` → payback 5.0. The cash flows actually recover the cost in year 8.
- **Fix:** `construction_years + cost / annual`.

### M4. `portfolio_financials` reports unknown revenue and profit as 0
- **Where:** `financials.py:72-73`.
- **Defect:** pandas `sum` of all-NaN values is 0.
- **Reproduction (in-memory):** FY 2081/82 with energy 50 GWh and no revenue → `revenue_npr 0.0, net_profit_npr 0`.
- **Fix:** Use `sum(min_count=1)`.

### M5. `financial_history` realised tariff becomes `inf` when a year's energy is 0
- **Where:** `financials.py:60`.
- **Reproduction (in-memory):** energy 0.0, revenue 1e6 → `realised_tariff_npr_kwh = inf`. `project_financials` is empty today.
- **Fix:** Replace 0 energy with NaN before dividing.

### M6. `dms_to_decimal` loses coordinates in other formats and accepts impossible minutes
- **Where:** `cleaners.py:138-144`.
- **Reproduction:**
  - `dms_to_decimal('27.4744')` → None.
  - `"27o 28'"` (no seconds) → None.
  - `'27 75 10'` → 28.25278 (75 minutes accepted).

  Current DoED files parse cleanly, so this is latent.
- **Fix:** Accept decimal and degree-minute forms, and reject minutes or seconds ≥ 60.

### M7. Validator duplicate check compares each name group only to its first member
- **Where:** `validators.py:79-82`.
- **Reproduction (in-memory):** `Mai 22 MW`, `Mai Cascade 7.0`, `Mai Cascade HPP 7.0` → no `duplicate-suspect`, although the last two are identical in name key and capacity.
- **Fix:** Compare all pairs within each group.

### M8. Excel links a plant's own PPA rate to the assumption cell when it happens to equal the blended assumption
- **Where:** `excel_builder.py:140`.
- **Defect:** A reported rate within 0.006 of the blend (e.g. 5.88 = the 4.80/8.40 standard at a 30% dry share) is turned into `=Assumptions!$B$7`. Editing the assumption then changes a "reported" figure.
- **Reproduction:** latent; `projects.ppa_rate_npr_per_kwh` is NULL for all 223 operational plants today.
- **Fix:** Link only when `ppa_rate_npr_per_kwh` is null, using the frame's `basis` and source columns rather than value equality.

---

## Checked and found correct
- Unit conversions:
  - `GWh × NPR/kWh = NPR million` (`financials.py:40`).
  - `estimate_energy_gwh` = MW × 8760 × CF / 1000.
  - The Excel energy formula `C*8.76*Assumptions!$B$6` matches it.
  - The Assumptions rows (B3 wet, B4 dry, B5 dry share, B6 CF, B7 blend) line up with the formulas, and the header and data row offsets (header row 3, data row 4) are correct.
  - `est_rev` per MW in `company_profiles.py:164-166` (×1e6 to NPR).
  - The `paidup_npr_m` and `op_mw_per_bn_paidup` scaling in `ownership.py`.
- `blended_tariff` weighting and the `irr` bisection: tests pass and `[-100,110]` → 10%.
- Excel Summary sheet `COUNTIF`/`SUMIF` ranges match the Projects sheet (header row 1, n rows), and the share-of-MW denominator is correct.
- BS/AD handling:
  - `bs_to_ad` guards zero parts and bad dates.
  - All `commissioning_year`, `expected_completion_year` and licence dates in the DB are AD and consistent with `commissioning_date`.
  - `_prev_fy` handles `2080/81` → `2079/80` and century rollover.
  - Standalone-quarter differencing and YoY guards for a non-positive base are correct.
- No double counting of a project across two listed companies: only one project (HP_019, both sides unlisted) has a developer different from its owner. `symbol = owner_symbol or developer_symbol` credits each plant once in the NEPSE exposure figures.
- Province and district consistency: 0 projects whose province disagrees with their district's province.
- The district-in-name heuristic (location step 4) matches the DoED district in 17 of 17 testable cases.
- Wikipedia rows only fill empty fields, and a stage only moves forward (`STATUS_RANK`).
- Claiming prevents two rows from the same DoED stage collapsing into one project.
- `technical.py`:
  - `capacity_by` share, `commissioning_timeline` cumulative total and `pipeline_by_year` mixed int/"Unknown" sort all work on real data.
  - `expiring_licences` gets Python `date` objects.
  - `forecast_capacity` adds under-construction MW to the 4,010 MW operational base correctly.
- `manual_import`:
  - `import_facts_csv` NULL matching (`== None` → IS NULL).
  - `import_company_financials_csv` fiscal year and quarter validation.
  - Shareholder upsert.
  - Enum parsing.
