# Nepal Hydropower Database System — Project Plan

## Project Overview
Comprehensive database tracking **ALL hydropower projects in Nepal** (operational, under-construction, planned) with:
- Financial data (cost, revenue, energy generation)
- Technical specifications (capacity, type, location, geology)
- Ownership & shareholding (for NEPSE stock tracking)
- Development timeline & regulatory status
- Environmental & social impact metrics

---

## Data Scope

### Projects to Track
1. **Operational** (~50+ projects) — currently generating electricity
2. **Under Construction** (~20+ projects) — in active development
3. **Planned/Approved** (~100+ projects) — awaiting funding/commencement
4. **Decommissioned/Suspended** (~5+ projects) — historical reference

### Data Points per Project

| Category | Fields |
|----------|--------|
| **Identification** | Project ID, Name (EN/NP), Acronym |
| **Location** | District, Province, Municipality, River, GPS coords |
| **Technical** | Capacity (MW), Type (RoR/Reservoir/Peaking), Turbine count & type, Head (m), Discharge (m³/s), Tunnel length (m), Design features |
| **Financial** | Project cost (USD/NPR), Annual revenue (est.), Energy generation (GWh/year), Power purchase rate ($/kWh), PPA terms |
| **Development** | Status, Start date, Commissioning date, Expected completion, License validity period |
| **Ownership** | Developer company, Owner, IPP/NEA/GoN/Private, Equity structure, Shareholding (if public listed) |
| **Grid** | Transmission line (kV), Substation connection, Grid status |
| **Regulatory** | DoED license #, PPA details, Environmental clearance, Social safeguard status |
| **Impact** | Estimated jobs created, CO₂ avoidance (t/year), Population benefited, Local economic impact |
| **News & Updates** | Latest status (2-3 recent news items), Key milestones |

---

## System Architecture

```
hydropower-nepal/
│
├── data/
│   ├── raw/                          # Scraped/raw source data
│   ├── processed/                    # Cleaned, standardized data
│   └── projects.db                   # SQLite database (or postgres)
│
├── src/
│   ├── __init__.py
│   ├── config.py                     # DB connection, settings
│   ├── models.py                     # SQLAlchemy ORM models
│   ├── database.py                   # DB initialization, migrations
│   ├── collectors/                   # Data collection modules
│   │   ├── __init__.py
│   │   ├── web_scraper.py           # DoED, Wikipedia, news scraping
│   │   ├── manual_import.py          # CSV/Excel import
│   │   └── api_client.py             # External APIs (if available)
│   ├── processors/                   # Data cleaning & validation
│   │   ├── __init__.py
│   │   ├── cleaners.py               # Text normalization, unit conversion
│   │   └── validators.py             # Data quality checks
│   ├── analytics/                    # Analysis & queries
│   │   ├── __init__.py
│   │   ├── financials.py             # Revenue, cost, ROI analysis
│   │   ├── technical.py              # Capacity, generation analysis
│   │   └── ownership.py              # Shareholding, NEPSE correlation
│   ├── cli.py                        # Command-line interface
│   └── dashboard.py                  # Web dashboard (Flask/FastAPI)
│
├── dashboards/
│   ├── index.html                    # Main dashboard
│   ├── templates/                    # HTML templates
│   ├── static/
│   │   ├── css/
│   │   ├── js/
│   │   └── data/
│   └── reports/                      # Generated reports
│
├── exports/
│   ├── templates/                    # Excel templates
│   ├── generated/                    # Generated Excel files
│   └── excel_builder.py              # Excel export logic
│
├── tests/
│   ├── test_models.py
│   ├── test_collectors.py
│   └── test_analytics.py
│
├── requirements.txt
├── README.md
└── main.py                           # Entry point
```

---

## Implementation Phases

### Phase 1: Foundation (Weeks 1-2)
- [ ] Set up project structure & Git repo *(structure done; not a git repository)*
- [x] Design & create SQLite database schema
- [x] Build ORM models (SQLAlchemy)
- [x] Create basic data import pipeline (CSV/manual entry)
- [x] Build initial dataset (50+ operational projects from web research)

### Phase 2: Data Collection (Weeks 3-4)
- [x] Web scraper for DoED, Wikipedia, company websites
- [x] News feed integration (hydropower news from Nepal sources)
- [x] Manual data validation workflow
- [x] Data cleaning & standardization

### Phase 3: CLI & Analytics (Weeks 5-6)
- [x] Command-line interface (view, filter, search projects)
- [x] Financial analysis tools (revenue modeling, cost breakdowns)
- [x] Technical analysis (capacity utilization, generation trends)
- [x] Ownership analysis (shareholding queries for NEPSE correlation)

### Phase 4: Web Dashboard (Weeks 7-8)
- [x] Interactive map visualization (Folium/Mapbox)
- [x] Project detail pages (full specs, financials, timelines)
- [x] Search & filter interface
- [x] Real-time updates from news feed
- [x] Performance metrics (total capacity, generation, revenue)

### Phase 5: Excel & Reporting (Weeks 9-10)
- [x] Excel export templates (project master list, financial summary, technical specs)
- [x] Automated report generation
- [x] NEPSE stock correlation reports (which hydropower stocks are exposed)
- [ ] Scheduling (daily/weekly updates) *(`refresh` command provided; scheduling is left to Task Scheduler/cron)*

### Phase 6: Enhancement & Deployment (Weeks 11+)
- [x] Add forecasting (revenue projections, capacity additions timeline)
- [ ] API layer (expose data via REST/GraphQL for integrations) *(REST done; GraphQL not built)*
- [ ] Mobile-friendly dashboard *(responsive CSS written; not tested on a device)*
- [x] Performance optimization
- [ ] Docker containerization *(Dockerfile written; never built or run)*

---


## Key Data Sources

| Source | Data Type | Method |
|--------|-----------|--------|
| **Department of Electricity Development (DoED)** | Regulatory, licenses, status | Web scraping, direct contact |
| **Wikipedia Hydropower Projects** | Technical specs, financials | Web scraping |
| **Company Websites** | Ownership, development updates | Web scraping, manual |
| **Nepal Electricity Authority (NEA)** | Grid connection, PPA terms, generation data | Public reports, web scraping |
| **News Sources** (Kathmandu Post, The Himalayan Times, etc.) | Project updates, milestones | RSS feeds, web scraping |
| **SEBON / Stock Exchange** | Shareholding, public listed companies | Direct scraping, regulatory filings |
| **World Bank, IFC Projects** | International financing, social impact | Project databases |

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| **Database** | SQLite (dev) / PostgreSQL (production) |
| **Backend** | Python 3.10+, SQLAlchemy ORM, Pandas |
| **Web Framework** | Flask or FastAPI |
| **Web Scraping** | BeautifulSoup4, Selenium (for JS-heavy sites) |
| **Visualization** | Folium (maps), Plotly (charts), Bootstrap (UI) |
| **Data Export** | openpyxl (Excel), pandas |
| **CLI** | Click or Typer (Python CLI library) |
| **Task Scheduling** | APScheduler (background jobs, news updates) |
| **Testing** | pytest |
| **Version Control** | Git + GitHub |
| **Deployment** | Docker, Railway/Render (free tier) or your server |

---

## Timeline & Effort

| Phase | Duration | Complexity | Dependencies |
|-------|----------|-----------|--------------|
| Foundation | 1-2 weeks | Low | Python basics |
| Data Collection | 2-3 weeks | Medium | Web scraping, data cleaning |
| CLI & Analytics | 2 weeks | Medium | SQL queries, Pandas |
| Web Dashboard | 2-3 weeks | Medium-High | HTML/CSS/JS, Flask/FastAPI |
| Excel & Reporting | 1-2 weeks | Low-Medium | openpyxl |
| **Total** | **10-12 weeks** | — | — |

**Note:** You can launch with Phase 3 (CLI + local data) and add the dashboard incrementally.

---

## Success Metrics

- [ ] Database contains ≥90% of Nepal's hydropower projects
- [ ] Data accuracy validated against DoED, company sources (95%+ accuracy)
- [ ] CLI provides sub-second queries on 500+ projects
- [ ] Dashboard loads in <2 seconds
- [ ] Excel exports generated in <10 seconds
- [ ] Daily news updates working automatically
- [ ] 50+ financial metrics computed & tracked per project
- [ ] NEPSE stock correlation analysis working

---

## Next Steps

1. Start with **Phase 1** → Build database schema & load initial data
2. Create initial dataset from web research (no scraper yet)
3. Test data model with 20-30 projects
4. Then build scraper & scale to full dataset
5. Build CLI for querying & analysis
6. Finally, layer web dashboard on top

Ready to start Phase 1? I'll create the database schema and ORM models next.
