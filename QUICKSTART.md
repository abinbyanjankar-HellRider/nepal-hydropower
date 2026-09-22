# Quick Start

## 1. Install and build the database (about 1 minute)

```bash
pip install -r requirements.txt
python main.py load-sample     # districts, rivers, reference companies
python main.py sync            # DoED + Wikipedia + NEPSE listing -> database
python main.py summary
```

## 2. Explore

```bash
python main.py web                                   # http://127.0.0.1:5000
python main.py analyze capacity --by province --status Operational
python main.py analyze nepse                         # NEPSE-listed companies and their capacity
python main.py analyze company CHCL                  # one company by symbol
python main.py analyze project HP_001                # everything about one project
python main.py analyze forecast --delay-years 2      # capacity scenario with slippage
python main.py export --format master                # Excel workbook in exports/generated/
```

## 3. Query from Python

```python
from src.database import db_manager
from src.models import Project, ProjectStatus
from sqlalchemy import select, func

with db_manager.session_scope() as s:
    big = s.scalars(select(Project)
                    .where(Project.status == ProjectStatus.OPERATIONAL, Project.capacity_mw > 50)
                    .order_by(Project.capacity_mw.desc())).all()
    for p in big:
        print(p.project_name_en, p.capacity_mw, p.developer.company_name if p.developer else "-")

    total = s.scalar(select(func.sum(Project.capacity_mw)).where(Project.status == ProjectStatus.OPERATIONAL))
    print(f"Operational: {total:,.0f} MW")
```

For DataFrame-based analysis use `src.analytics.load_projects_df(session)`: one row per project with district,
developer, NEPSE symbol and status already joined.

## 4. Add your own verified data

Nothing public provides financials or shareholders in bulk, so load them from annual reports:

```bash
python main.py import-csv my_financials.csv --kind financials
python main.py import-csv my_holders.csv    --kind shareholders
python main.py import-csv my_projects.csv   --kind projects
```

Column formats are in the [README](README.md#csv-formats). Bad rows are reported and skipped, not fatal.

## 5. Keep it fresh

```bash
python main.py refresh         # backup + sync + news + validate + export
```

Schedule that daily with Windows Task Scheduler or cron.

## Check data quality

```bash
python main.py validate-data --details
```
Review `data/processed/sync_conflicts.txt` after each sync: it lists where sources disagree.

## Troubleshooting
- **`No projects in the database`**: run `python main.py sync`.
- **Schema errors after pulling changes**: SQLite is recreated in development: delete `data/projects.db`, then
  `load-sample` and `sync`.
- **Sync fails with a network error**: the sources are live websites. Re-run; scrape snapshots from earlier runs
  are in `data/raw/`.
- **Map/charts blank**: they load Leaflet, Chart.js and map tiles from CDNs, so the browser needs internet access.
- **Garbled Unicode in a Windows console**: `set PYTHONIOENCODING=utf-8`.
