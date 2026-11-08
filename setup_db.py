import random
from datetime import datetime, timedelta
import mysql.connector
import config

THRESHOLD = {"Germination": 28, "Tillering": 25, "Grand growth": 30, "Maturity": 20}
PLOTS = [  # id, code, farmer, village, soil, acres, stage, lat, lon
    (1, "P-101", "Ramesh Patil", "Yalakeri", "Black", 5.6, "Grand growth", 16.41, 74.88),
    (2, "P-102", "Sunita Naik", "Hukkeri", "Red", 3.2, "Tillering", 16.23, 74.60),
    (3, "P-103", "Mahesh Kamble", "Sankeshwar", "Black", 4.8, "Grand growth", 16.26, 74.49),
    (4, "P-104", "Lakshmi Hiremath", "Sameerwadi", "Loam", 2.5, "Germination", 16.39, 75.10),
    (5, "P-105", "Basavaraj Kore", "Gokak", "Red", 6.1, "Maturity", 16.17, 74.82),
    (6, "P-106", "Anita Jadhav", "Mudalagi", "Black", 3.9, "Tillering", 16.20, 74.76),
    (7, "P-107", "Shivanand Desai", "Konnur", "Loam", 7.4, "Grand growth", 16.35, 74.93),
    (8, "P-108", "Geeta Mane", "Ghataprabha", "Red", 4.1, "Tillering", 16.15, 74.78),
]
DRY = {0, 3, 6}      # plots left unirrigated so the demo shows stress
OFFLINE = 7          # last plot stops reporting (sensor fault demo)


def gen_readings(now, seed=42, steps=80):
    random.seed(seed)
    rows = []
    for i, p in enumerate(PLOTS):
        m, batt = random.uniform(31, 38), random.uniform(60, 90)
        limit = THRESHOLD[p[6]]
        for s in range(steps):
            t = now - timedelta(hours=6 * (steps - 1 - s))
            m -= random.uniform(0.2, 0.45)
            if random.random() < 0.04:
                m += random.uniform(6, 12)                      # rain event
            if i not in DRY and m < limit - 2:
                m += 14                                         # farmer irrigates
            m = max(8.0, min(45.0, m))
            batt = max(8, min(100, batt + random.uniform(-2, 2)))
            if i == OFFLINE and s > steps - 6:
                continue
            rows.append((p[0], t.replace(microsecond=0), round(m, 1), round(m + 2, 1), round(random.gauss(28, 2.5), 1),
                         int(random.uniform(40, 75)), random.choice([0, 0, 0, 0, 3, 12]),
                         int(batt), random.randint(70, 95)))
    return rows


def main():
    mydb = mysql.connector.connect(host=config.DB_HOST, port=config.DB_PORT,
                                   user=config.DB_USER, password=config.DB_PASSWORD)
    cursor = mydb.cursor()
    cursor.execute("CREATE DATABASE IF NOT EXISTS " + config.DB_NAME)
    cursor.execute("USE " + config.DB_NAME)
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table in ("irrigation_log", "advisories", "readings", "plots"):
        cursor.execute("DROP TABLE IF EXISTS " + table)
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

    cursor.execute("""CREATE TABLE plots (
        id INT PRIMARY KEY, code VARCHAR(10) NOT NULL UNIQUE, farmer VARCHAR(60) NOT NULL,
        village VARCHAR(40), soil VARCHAR(20), area_acres DECIMAL(4,1), crop_stage VARCHAR(20) NOT NULL,
        lat DECIMAL(8,5), lon DECIMAL(8,5))""")
    cursor.execute("""CREATE TABLE readings (
        id BIGINT PRIMARY KEY AUTO_INCREMENT, plot_id INT NOT NULL, recorded_at DATETIME NOT NULL,
        moisture_30 DECIMAL(4,1) NOT NULL, moisture_60 DECIMAL(4,1), soil_temp DECIMAL(4,1), humidity INT,
        rain_fc_mm DECIMAL(5,1) DEFAULT 0, battery_pct INT, signal_pct INT,
        INDEX plot_time (plot_id, recorded_at), FOREIGN KEY (plot_id) REFERENCES plots(id))""")
    cursor.execute("""CREATE TABLE advisories (
        id INT PRIMARY KEY AUTO_INCREMENT, plot_id INT NOT NULL, issued_on DATE NOT NULL,
        action VARCHAR(10) NOT NULL, hours DECIMAL(3,1) NOT NULL, status VARCHAR(10) NOT NULL DEFAULT 'pending',
        reviewed_at DATETIME NULL, UNIQUE KEY one_per_day (plot_id, issued_on),
        FOREIGN KEY (plot_id) REFERENCES plots(id))""")
    cursor.execute("""CREATE TABLE irrigation_log (
        id INT PRIMARY KEY AUTO_INCREMENT, plot_id INT NOT NULL, hours DECIMAL(3,1) NOT NULL,
        logged_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (plot_id) REFERENCES plots(id))""")

    cursor.executemany("INSERT INTO plots VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", PLOTS)
    cursor.executemany("""INSERT INTO readings (plot_id, recorded_at, moisture_30, moisture_60, soil_temp,
        humidity, rain_fc_mm, battery_pct, signal_pct) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                       gen_readings(datetime.now()))
    mydb.commit()
    print("Database " + config.DB_NAME + " successfully initialized!")
    cursor.close()
    mydb.close()


if __name__ == "__main__":
    main()
