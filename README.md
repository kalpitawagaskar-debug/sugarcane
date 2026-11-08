# Sugarcane – AI Irrigation Advisory

Full-stack demo for the KJS-AGR-01 use case. Flask API + MySQL (via `mysql.connector`, same pattern as the SQL lab) + plain HTML/CSS/JS.

## Run it

1. Start MySQL (default: user `root`, password `mysql`, port 3306). Change them in `config.py` or with `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`.
2. `pip install -r requirements.txt`
3. `python setup_db.py` (creates `sugarcane_db`, the tables and sample data)
4. `python app.py`, then open http://localhost:3000

## Interactive features

Filter, search and sort plots; click the KPI cards to filter; hover the chart; switch 3/7/14-day range; what-if sliders; language switch; farmer irrigation log; agronomist approval console; "Simulate sensor stream" that writes live readings to MySQL.

## How it maps to the use case

| Use case layer | In this project |
|---|---|
| Field data acquisition (IoT) | `readings` table, `POST /api/ingest` |
| Data integration | `plots` (farmer, soil, crop stage) joined with `readings` |
| AI analytics | `outlook()`: 7-day moisture forecast, next irrigation date, yield-loss estimate |
| Decision support | `decide()`: irrigate now / soon / adequate / rain, hours, fertigation, pump window |
| Generative AI advisory | `TEXT` in `app.py`: English, Hindi, Kannada, Marathi (rule-based stand-in for an LLM) |
| Human in the loop | Advisories start as drafts; an agronomist approves or rejects them |
| End users | "View as" switch: farmer, agronomist, factory manager |

Tables: `plots`, `readings`, `advisories`, `irrigation_log`.

## Notes

- All data is sample data. Three plots are left dry and one sensor is silent on purpose.
- The forecast and thresholds are simple rules; swap `decide()` and `outlook()` for trained models.
- No login. The "View as" switch is a demo, not security.
