from datetime import date, datetime, timedelta
from decimal import Decimal

import mysql.connector
from flask import Flask, jsonify, request, send_from_directory

import config

app = Flask(__name__, static_folder="public", static_url_path="")

THRESHOLD = {"Germination": 28, "Tillering": 25, "Grand growth": 30, "Maturity": 20}
FERT = {"Germination": "DAP 15 kg/acre", "Tillering": "Urea 20 kg/acre",
        "Grand growth": "Urea 20 kg + MOP 15 kg/acre", "Maturity": "No fertigation (ripening)"}
PUMP_WINDOW = "5:00 AM - 8:00 AM"   # electricity availability, replace with feeder data
TEXT = {
    "now": {"en": "Irrigate tomorrow morning for {h} hours", "hi": "कल सुबह {h} घंटे सिंचाई करें",
            "kn": "ನಾಳೆ ಬೆಳಿಗ್ಗೆ {h} ಗಂಟೆ ನೀರು ಹಾಯಿಸಿ", "mr": "उद्या सकाळी {h} तास सिंचन करा"},
    "soon": {"en": "Irrigate in 2 days for {h} hours", "hi": "2 दिन में {h} घंटे सिंचाई करें",
             "kn": "2 ದಿನಗಳಲ್ಲಿ {h} ಗಂಟೆ ನೀರು ಹಾಯಿಸಿ", "mr": "2 दिवसांत {h} तास सिंचन करा"},
    "ok": {"en": "Soil moisture is adequate; no irrigation needed", "hi": "मिट्टी में पर्याप्त नमी है; सिंचाई की जरूरत नहीं",
           "kn": "ಮಣ್ಣಿನಲ್ಲಿ ಸಾಕಷ್ಟು ತೇವಾಂಶವಿದೆ; ನೀರು ಬೇಕಿಲ್ಲ", "mr": "जमिनीत पुरेसा ओलावा आहे; सिंचनाची गरज नाही"},
    "rain": {"en": "Rainfall is expected; irrigation is not required today", "hi": "बारिश की संभावना है; आज सिंचाई की जरूरत नहीं",
             "kn": "ಮಳೆ ನಿರೀಕ್ಷೆಯಿದೆ; ಇಂದು ನೀರು ಬೇಕಿಲ್ಲ", "mr": "पावसाची शक्यता आहे; आज सिंचनाची गरज नाही"},
}


# ---------- database helpers ----------

def get_connection():
    return mysql.connector.connect(host=config.DB_HOST, port=config.DB_PORT, user=config.DB_USER,
                                   password=config.DB_PASSWORD, database=config.DB_NAME)


def clean(row):
    out = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            value = float(value)
        elif isinstance(value, (date, datetime)):
            value = value.isoformat()
        out[key] = value
    return out


def run(sql, params=(), write=False):
    mydb = get_connection()
    cursor = mydb.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        if write:
            mydb.commit()
            return cursor.lastrowid, cursor.rowcount
        return [clean(row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        mydb.close()


# ---------- irrigation logic (rule-based stand-in for the ML models) ----------

def decide(moisture, rain_mm, stage):
    limit = THRESHOLD.get(stage, 25)
    if rain_mm >= 10:
        return "rain", 0.0
    if moisture >= limit + 10:
        return "ok", 0.0
    if moisture >= limit:
        return "soon", round((limit + 10 - moisture) * 1.25 / 10, 1)
    return "now", round((limit + 10 - moisture) * 1.25 / 5, 1)


def say(action, hours, lang):
    return TEXT[action].get(lang, TEXT[action]["en"]).format(h=hours)


def latest_plots():
    rows = run("""SELECT p.*, r.moisture_30, r.moisture_60, r.soil_temp, r.humidity, r.rain_fc_mm,
                  r.battery_pct, r.signal_pct, r.recorded_at,
                  TIMESTAMPDIFF(MINUTE, r.recorded_at, NOW()) AS minutes_ago
                  FROM plots p
                  LEFT JOIN readings r ON r.id = (SELECT MAX(id) FROM readings WHERE plot_id = p.id)
                  ORDER BY p.code""")
    for p in rows:
        if p["moisture_30"] is None:
            p["action"], p["hours"], p["sensor"], p["threshold"] = "ok", 0, "never", THRESHOLD[p["crop_stage"]]
            continue
        p["action"], p["hours"] = decide(p["moisture_30"], p["rain_fc_mm"], p["crop_stage"])
        p["threshold"] = THRESHOLD[p["crop_stage"]]
        p["sensor"] = "online" if p["minutes_ago"] <= 600 else "offline"
    return rows


def outlook(plot_id):
    plot = next((p for p in latest_plots() if p["id"] == plot_id), None)
    if plot is None:
        return None
    daily = run("""SELECT DATE(recorded_at) AS day, moisture_30 AS m FROM readings
                   WHERE id IN (SELECT MAX(id) FROM readings WHERE plot_id = %s
                                AND recorded_at > NOW() - INTERVAL 14 DAY GROUP BY DATE(recorded_at))
                   ORDER BY recorded_at""", (plot_id,))
    vals = [d["m"] for d in daily]
    drops = [a - b for a, b in zip(vals, vals[1:]) if b < a]      # ignore irrigation/rain jumps
    rate = max(0.4, min(2.5, sum(drops) / len(drops))) if drops else 1.0
    start, limit = plot["moisture_30"], plot["threshold"]
    forecast, nxt = [], None
    for k in range(1, 8):
        pred = round(max(8, start - rate * k), 1)
        forecast.append({"lead": k, "date": (date.today() + timedelta(days=k)).isoformat(), "pred": pred})
        if nxt is None and pred < limit:
            nxt = {"lead": k, "date": forecast[-1]["date"]}
    stress_days = sum(1 for v in vals if v < limit)
    return {"plot": plot, "forecast": forecast, "next_irrigation": nxt, "rate_per_day": round(rate, 2),
            "stress_days": stress_days, "yield_loss_pct": min(25.0, round(stress_days * 1.5, 1))}


# ---------- pages ----------

@app.route("/")
def home():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    row = run("""SELECT VERSION() AS version, DATABASE() AS db_name,
                 (SELECT COUNT(*) FROM plots) AS plots, (SELECT COUNT(*) FROM readings) AS readings,
                 (SELECT COUNT(*) FROM advisories) AS advisories,
                 (SELECT COUNT(*) FROM irrigation_log) AS irrigations""")[0]
    row.update(host=config.DB_HOST, port=config.DB_PORT, user=config.DB_USER, ok=True)
    return jsonify(row)


# ---------- monitoring ----------

@app.route("/api/plots")
def plots():
    return jsonify(latest_plots())


@app.route("/api/summary")
def summary():
    ps = latest_plots()
    live = [p for p in ps if p["moisture_30"] is not None]
    pending = run("SELECT COUNT(*) AS n FROM advisories WHERE issued_on = CURDATE() AND status = 'pending'")[0]["n"]
    return jsonify({
        "plots": len(ps), "acres": round(sum(p["area_acres"] for p in ps), 1),
        "avg_moisture": round(sum(p["moisture_30"] for p in live) / max(len(live), 1), 1),
        "need_water": sum(1 for p in ps if p["hours"] > 0),
        "water_hours": round(sum(p["hours"] for p in ps), 1),
        "offline": sum(1 for p in ps if p["sensor"] != "online"), "pending": pending})


@app.route("/api/plots/<int:plot_id>/outlook")
def plot_outlook(plot_id):
    data = outlook(plot_id)
    if data is None:
        return jsonify({"error": "Unknown plot"}), 404
    return jsonify(data)


@app.route("/api/plots/<int:plot_id>/series")
def series(plot_id):
    days = min(request.args.get("days", 14, type=int), 90)
    return jsonify(run("""SELECT recorded_at, moisture_30, moisture_60 FROM readings
                          WHERE plot_id = %s AND recorded_at > NOW() - INTERVAL %s DAY
                          ORDER BY recorded_at""", (plot_id, days)))


@app.route("/api/plots/<int:plot_id>/advisory")
def advisory(plot_id):
    lang = request.args.get("lang", "en")
    data = outlook(plot_id)
    if data is None:
        return jsonify({"error": "Unknown plot"}), 404
    p = data["plot"]
    official = run("""SELECT action, hours FROM advisories WHERE plot_id = %s AND issued_on = CURDATE()
                      AND status = 'approved'""", (plot_id,))
    action, hours = (official[0]["action"], official[0]["hours"]) if official else (p["action"], p["hours"])
    irrigating = action in ("now", "soon")
    return jsonify({"plot": p["code"], "official": bool(official), "action": action, "hours": hours,
                    "text": say(action, hours, lang),
                    "fertigation": FERT[p["crop_stage"]] + " after irrigation" if irrigating else "Not needed until the next irrigation",
                    "pump_window": PUMP_WINDOW if irrigating else "Pump can stay off",
                    "yield_loss_pct": data["yield_loss_pct"], "next_irrigation": data["next_irrigation"]})


@app.route("/api/whatif")
def whatif():
    try:
        m, rain = float(request.args["moisture"]), float(request.args.get("rain", 0))
        stage = request.args.get("stage", "Grand growth")
    except (KeyError, ValueError):
        return jsonify({"error": "Send moisture, rain and stage."}), 400
    action, hours = decide(m, rain, stage)
    return jsonify({"action": action, "hours": hours, "threshold": THRESHOLD.get(stage, 25),
                    "text": say(action, hours, request.args.get("lang", "en"))})


# ---------- IoT ingestion ----------

@app.route("/api/ingest", methods=["POST"])
def ingest():
    d = request.get_json(silent=True) or {}
    try:
        plot_id = int(d.get("plot_id"))
        m30 = float(d.get("moisture_30"))
        if not 0 <= m30 <= 100:
            raise ValueError
        m60 = float(d.get("moisture_60", m30 + 2))
        temp = float(d.get("soil_temp", 28))
        hum = int(d.get("humidity", 60))
        rain = float(d.get("rain_fc_mm", 0))
        batt = int(d.get("battery_pct", 100))
        sig = int(d.get("signal_pct", 100))
    except (TypeError, ValueError):
        return jsonify({"error": "Send plot_id and moisture_30 (0 to 100)."}), 400
    if not run("SELECT id FROM plots WHERE id = %s", (plot_id,)):
        return jsonify({"error": "Unknown plot."}), 404
    run("""INSERT INTO readings (plot_id, recorded_at, moisture_30, moisture_60, soil_temp, humidity,
           rain_fc_mm, battery_pct, signal_pct) VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s, %s)""",
        (plot_id, m30, m60, temp, hum, rain, batt, sig), write=True)
    return jsonify({"ok": True}), 201


# ---------- farmer irrigation log ----------

@app.route("/api/irrigation-log")
def irrigation_log():
    return jsonify(run("""SELECT l.id, p.code, p.farmer, l.hours, l.logged_at FROM irrigation_log l
                          JOIN plots p ON p.id = l.plot_id ORDER BY l.id DESC LIMIT 6"""))


@app.route("/api/plots/<int:plot_id>/irrigated", methods=["POST"])
def irrigated(plot_id):
    try:
        hours = float((request.get_json(silent=True) or {}).get("hours"))
        if not 0 < hours <= 12:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Enter irrigation hours between 0 and 12."}), 400
    if not run("SELECT id FROM plots WHERE id = %s", (plot_id,)):
        return jsonify({"error": "Unknown plot."}), 404
    run("INSERT INTO irrigation_log (plot_id, hours) VALUES (%s, %s)", (plot_id, hours), write=True)
    return jsonify({"ok": True}), 201


# ---------- human-in-the-loop advisories ----------

@app.route("/api/advisories")
def advisories():
    lang = request.args.get("lang", "en")
    rows = run("""SELECT a.id, a.plot_id, p.code, p.farmer, p.village, a.action, a.hours, a.status
                  FROM advisories a JOIN plots p ON p.id = a.plot_id WHERE a.issued_on = CURDATE()
                  ORDER BY a.status DESC, p.code""")
    for r in rows:
        r["text"] = say(r["action"], r["hours"], lang)
    return jsonify(rows)


@app.route("/api/advisories/generate", methods=["POST"])
def generate():
    created = 0
    for p in latest_plots():
        if p["action"] in ("now", "soon"):
            _, n = run("""INSERT IGNORE INTO advisories (plot_id, issued_on, action, hours)
                          VALUES (%s, CURDATE(), %s, %s)""", (p["id"], p["action"], p["hours"]), write=True)
            created += n
    return jsonify({"created": created}), 201


@app.route("/api/advisories/<int:adv_id>/decision", methods=["POST"])
def decide_advisory(adv_id):
    decision = (request.get_json(silent=True) or {}).get("decision")
    if decision not in ("approved", "rejected"):
        return jsonify({"error": "Decision must be approved or rejected."}), 400
    _, changed = run("UPDATE advisories SET status = %s, reviewed_at = NOW() WHERE id = %s AND status = 'pending'",
                     (decision, adv_id), write=True)
    if not changed:
        return jsonify({"error": "This advisory was already reviewed."}), 409
    return jsonify({"ok": True})


@app.errorhandler(mysql.connector.Error)
def db_error(err):
    print(err)
    return jsonify({"ok": False, "detail": str(err), "error": "Database error. Check that MySQL is running, "
                    "settings in config.py are correct and setup_db.py has been run."}), 500


if __name__ == "__main__":
    app.run(debug=True, port=3000)
