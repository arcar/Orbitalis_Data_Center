import sqlite3
import duckdb
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STAR_DB = DATA_DIR / "base_etoile.duckdb"
BASE_SQLITE = DATA_DIR / "base_analytique.db"


def lire_sqlite():
    src = sqlite3.connect(BASE_SQLITE)

    query_equip = """
        SELECT e.equipement_id, m.type_equipement, m.puissance_nominale_w,
               m.rendement_nominal, m.duree_vie_annee, e.date_installation,
               s.nom AS site
        FROM equipement e
        LEFT JOIN site s   ON s.site_id   = e.site_id
        LEFT JOIN modele m ON m.modele_id = e.modele_id;"""

    query_alarm = """
        SELECT DISTINCT type_alarme_id, severite FROM alarme;"""

    # ADAPTER : noms de colonnes de la table alarme
    query_alarm_detail = """
        SELECT equipement_id, timestamp_alarme, type_alarme_id, severite
        FROM alarme;"""

    query_temps = "SELECT timestamp_telemetrie FROM telemetrie;"

    query_maint_detail = """
        SELECT equipement_id, type_intervention, date_debut, date_fin,
               CAST(julianday(date_fin) - julianday(date_debut) AS INTEGER) AS duree
        FROM maintenance;"""

    query_maint = """
        SELECT DISTINCT type_intervention,
               CAST(julianday(date_fin) - julianday(date_debut) AS INTEGER) AS duree
        FROM maintenance
        ORDER BY type_intervention, duree;"""

    query_prod = "SELECT * FROM telemetrie;"

    query_orbit = """
        SELECT timestamp_orbite, phase,
               rayonnement_solaire_w_m2, temperature_ambiante_c
        FROM mesures_orbite;"""

    try:
        df_equip        = pd.read_sql_query(query_equip, src)
        df_alarm        = pd.read_sql_query(query_alarm, src)
        df_alarm_detail = pd.read_sql_query(query_alarm_detail, src)
        df_temps        = pd.read_sql_query(query_temps, src)
        df_maint_detail = pd.read_sql_query(query_maint_detail, src)
        df_maint        = pd.read_sql_query(query_maint, src)
        df_prod         = pd.read_sql_query(query_prod, src)
        df_orbit        = pd.read_sql_query(query_orbit, src)
    finally:
        src.close()

    print(df_prod.shape, df_orbit.shape)
    return (df_equip, df_alarm, df_alarm_detail, df_temps,
            df_maint_detail, df_maint, df_prod, df_orbit)


def creer_db(df_equip, df_alarm, df_alarm_detail, df_temps,
             df_maint_detail, df_maint, df_prod, df_orbit):

    con = duckdb.connect(str(STAR_DB))
    for nom, df in [("df_equip", df_equip), ("df_alarm", df_alarm),
                    ("df_alarm_detail", df_alarm_detail),
                    ("df_temps", df_temps),
                    ("df_maint_detail", df_maint_detail),
                    ("df_maint", df_maint), ("df_prod", df_prod),
                    ("df_orbit", df_orbit)]:
        con.register(nom, df)

    for t in ["fait_production", "dim_temps", "dim_maintenance",
              "dim_equipement", "dim_alarme"]:
        con.execute(f"DROP TABLE IF EXISTS {t}")

    # ---------- Tables ----------
    con.execute("DROP SEQUENCE IF EXISTS seq_maintenance")
    con.execute("CREATE SEQUENCE seq_maintenance START 1")
    con.execute("""
    CREATE TABLE dim_maintenance (
        maintenance_id    BIGINT PRIMARY KEY DEFAULT nextval('seq_maintenance'),
        type_maintenance  VARCHAR,
        duree             INTEGER
    )""")

    con.execute("""
    CREATE TABLE dim_temps (
        temps_id BIGINT PRIMARY KEY,   -- AAAAMMJJHHMM
        date DATE, annee INTEGER, mois INTEGER, jour INTEGER,
        heure INTEGER, minute INTEGER
    )""")

    con.execute("""
    CREATE TABLE dim_equipement (
        equipement_id         VARCHAR PRIMARY KEY,
        type_equipement       VARCHAR,
        puissance_nominale_w  INTEGER,
        rendement_nominal     DECIMAL(15,2),
        duree_vie_annee       INTEGER,
        date_installation     DATE,
        site                  VARCHAR
    )""")

    con.execute("DROP SEQUENCE IF EXISTS seq_alarme")
    con.execute("CREATE SEQUENCE seq_alarme START 1")
    con.execute("""
    CREATE TABLE dim_alarme (
        alarme_id    BIGINT PRIMARY KEY DEFAULT nextval('seq_alarme'),
        type_alarme  VARCHAR,
        severite     VARCHAR
    )""")

    con.execute("""
    CREATE TABLE fait_production (
        production_id             INTEGER PRIMARY KEY,
        maintenance_id            BIGINT  REFERENCES dim_maintenance(maintenance_id),
        temps_id                  BIGINT  REFERENCES dim_temps(temps_id),
        alarme_id                 BIGINT  REFERENCES dim_alarme(alarme_id),
        equipement_id             VARCHAR REFERENCES dim_equipement(equipement_id),
        puissance_w               DECIMAL(15,2),
        temperature_c             DECIMAL(15,2),
        rayonnement               DECIMAL(15,2),
        tension_v                 DECIMAL(15,2),
        courant_a                 DECIMAL(15,2),
        performance               DECIMAL(15,2),
        perte_production          DECIMAL(15,2),
        ratio_vie_equipement      DECIMAL(15,2),
        phase                     VARCHAR,
        rayonnement_solaire_w_m2  DECIMAL(15,2),
        temperature_ambiante_c    DECIMAL(15,2)
    )""")

    # ---------- Dimensions ----------
    con.execute("""
        INSERT INTO dim_equipement
        SELECT equipement_id, type_equipement, puissance_nominale_w,
               rendement_nominal, duree_vie_annee,
               CAST(substr(date_installation, 1, 10) AS DATE), site
        FROM df_equip""")

    con.execute("""
        INSERT INTO dim_alarme (type_alarme, severite)
        SELECT DISTINCT type_alarme_id, severite FROM df_alarm""")

    con.execute("""
        INSERT INTO dim_temps
        SELECT CAST(strftime(ts, '%Y%m%d%H%M') AS BIGINT),
               CAST(ts AS DATE),
               year(ts), month(ts), day(ts), hour(ts), minute(ts)
        FROM (SELECT DISTINCT CAST(substr(timestamp_telemetrie, 1, 19) AS TIMESTAMP) AS ts
              FROM df_temps)""")

    con.execute("""
        INSERT INTO dim_maintenance (type_maintenance, duree)
        SELECT DISTINCT type_intervention, duree FROM df_maint
        ORDER BY type_intervention, duree""")

    # ---------- Faits ----------
    con.execute("""
    INSERT INTO fait_production (
        production_id, maintenance_id, temps_id, alarme_id, equipement_id,
        puissance_w, temperature_c, rayonnement, tension_v, courant_a,
        performance, perte_production, ratio_vie_equipement,
        phase, rayonnement_solaire_w_m2, temperature_ambiante_c
    )
    WITH prod AS (
        SELECT *, CAST(substr(timestamp_telemetrie, 1, 19) AS TIMESTAMP) AS ts
        FROM df_prod
    ),
    orb AS (
        SELECT CAST(substr(timestamp_orbite, 1, 19) AS TIMESTAMP) AS ts_o,
               phase, rayonnement_solaire_w_m2, temperature_ambiante_c
        FROM df_orbit
    ),
    prod_orb AS (
        SELECT p.*, o.phase, o.rayonnement_solaire_w_m2, o.temperature_ambiante_c
        FROM prod p
        ASOF LEFT JOIN orb o ON p.ts >= o.ts_o
    ),
    maint AS (
        SELECT m.equipement_id,
               CAST(substr(m.date_debut, 1, 10) AS DATE) AS d_debut,
               CAST(substr(m.date_fin,   1, 10) AS DATE) AS d_fin,
               dm.maintenance_id
        FROM df_maint_detail m
        JOIN dim_maintenance dm
          ON dm.type_maintenance = m.type_intervention AND dm.duree = m.duree
    ),
    alarm AS (
        SELECT a.equipement_id,
               time_bucket(INTERVAL 5 MINUTE,
                           CAST(substr(a.timestamp_alarme, 1, 19) AS TIMESTAMP)) AS ts_b,
               arg_max(da.alarme_id,
                       CASE a.severite WHEN 'URGENCE' THEN 4 WHEN 'critical' THEN 3
                                       WHEN 'warning' THEN 2 ELSE 1 END) AS alarme_id
        FROM df_alarm_detail a
        JOIN dim_alarme da
          ON da.type_alarme = a.type_alarme_id AND da.severite = a.severite
        GROUP BY a.equipement_id, ts_b
    )
    SELECT
        row_number() OVER (ORDER BY po.ts, po.equipement_id),
        mt.maintenance_id,
        CAST(strftime(po.ts, '%Y%m%d%H%M') AS BIGINT),
        al.alarme_id,
        po.equipement_id,
        po.puissance_w, po.temperature_c, po.rayonnement,
        po.tension_v, po.courant_a,
        po.puissance_w / NULLIF(e.puissance_nominale_w, 0),
        GREATEST(e.puissance_nominale_w - po.puissance_w, 0),
        (date_diff('day', e.date_installation, current_date) * 100.0)
            / (e.duree_vie_annee * 365),
        po.phase, po.rayonnement_solaire_w_m2, po.temperature_ambiante_c
    FROM prod_orb po
    JOIN dim_equipement e ON e.equipement_id = po.equipement_id
    LEFT JOIN maint mt
      ON mt.equipement_id = po.equipement_id
     AND CAST(po.ts AS DATE) BETWEEN mt.d_debut AND mt.d_fin
    LEFT JOIN alarm al
      ON al.equipement_id = po.equipement_id AND al.ts_b = po.ts
    """)

    print("fait_production :", con.execute(
        "SELECT count(*) FROM fait_production").fetchone()[0],
        "| df_prod :", len(df_prod))
    print("avec alarme :", con.execute(
        "SELECT count(*) FROM fait_production WHERE alarme_id IS NOT NULL").fetchone()[0])
    print(con.execute("""
    SELECT equipement_id, temps_id, count(*) AS n
    FROM fait_production
    GROUP BY ALL HAVING count(*) > 1
    LIMIT 5
""").fetchdf())
    print(con.execute("""
    SELECT annee, mois, count(*) AS nb_mesures
    FROM fait_production f JOIN dim_temps t USING (temps_id)
    GROUP BY ALL ORDER BY 1, 2
""").fetchdf())

    print(df_maint_detail[df_maint_detail.equipement_id == "EQ-006"]
      .sort_values("date_debut"))

    print(df_alarm_detail["timestamp_alarme"].agg(["min", "max"]))
    print(df_prod.groupby(["equipement_id", "timestamp_telemetrie"])
      .size().loc[lambda s: s > 1].head(10))
    con.close()


def main():
    donnees = lire_sqlite()
    creer_db(*donnees)


if __name__ == "__main__":
    main()