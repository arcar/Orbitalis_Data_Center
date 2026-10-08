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
    query_equip = """ SELECT e.equipement_id, m.type_equipement, m.puissance_nominale_w,  m.rendement_nominal, m.duree_vie_annee ,e.date_installation, s.nom AS site
     FROM equipement e
      LEFT JOIN site s ON s.site_id = e.site_id 
      LEFT JOIN modele m ON m.modele_id = e.modele_id;"""

    query_alarm = """ SELECT DISTINCT type_alarme_id, severite
         FROM alarme;"""

    query_temps = """ SELECT timestamp_telemetrie
             FROM telemetrie;"""

    query_maint = """ SELECT DISTINCT type_intervention, CAST(julianday(date_fin) - julianday(date_debut) AS INTEGER) AS duree 
            FROM maintenance 
            ORDER BY type_intervention, duree;"""

    try:
        df_equip = pd.read_sql_query(query_equip, src)
        df_alarm = pd.read_sql_query(query_alarm, src)
        df_temps = pd.read_sql_query(query_temps, src)
        df_maint = pd.read_sql_query(query_maint, src)
    finally:
        src.close()
        print (df_equip)
        print (df_alarm)
        print (df_temps)
        print (df_maint)
    return df_equip, df_alarm, df_temps, df_maint


def creer_db(df_equip, df_alarm, df_temps, df_maint):

    con = duckdb.connect(STAR_DB)
    con.register("df_equip",df_equip)
    con.register("df_alarm",df_alarm)
    con.register("df_temps",df_temps)
    con.register("df_maint",df_maint)

    # con.execute("DROP TABLE IF EXISTS fait_production")
    con.execute("DROP TABLE IF EXISTS dim_temps")
    con.execute("DROP TABLE IF EXISTS dim_maintenance")
    con.execute("DROP TABLE IF EXISTS dim_equipement")
    con.execute("DROP TABLE IF EXISTS dim_alarme")

    # CREATION tables

    # Dim_maintenance

    con.execute("DROP SEQUENCE IF EXISTS seq_maintenance")
    con.execute("CREATE SEQUENCE seq_maintenance START 1")
    con.execute("""
    CREATE TABLE dim_maintenance (
        maintenance_id      BIGINT PRIMARY KEY DEFAULT nextval('seq_maintenance'),
        type_maintenance    VARCHAR,
        duree               INTEGER
    )
    """)

    #Dim temps
    con.execute("""
    CREATE TABLE dim_temps (
        temps_id        BIGINT PRIMARY KEY,   -- AAAAMMJJHHMM
        date            DATE,
        annee           INTEGER,
        mois            INTEGER,
        jour            INTEGER,
        heure           INTEGER,
        minute          INTEGER
        
    )
    """)

    #Dim equipement
    con.execute("""
    CREATE TABLE dim_equipement (
        equipement_id           VARCHAR PRIMARY KEY,
        type_equipement         VARCHAR,
        puissance_nominale_w    INTEGER,
        rendement_nominal       DECIMAL(15,2),
        duree_vie_annee         INTEGER,
        date_installation       DATE,
        site                    VARCHAR
        
    )
    """)

    #Dim alarme
    con.execute("DROP SEQUENCE IF EXISTS seq_alarme")
    con.execute("CREATE SEQUENCE seq_alarme START 1")
    con.execute("""
    CREATE TABLE dim_alarme (
        alarme_id           BIGINT PRIMARY KEY DEFAULT nextval('seq_alarme'),
        type_alarme         VARCHAR,
        severite            VARCHAR
        
    )
    """)


#     #fait trajet
#     con.execute("""
# CREATE TABLE fait_production (
#     production_id               INTEGER PRIMARY KEY,
#     maintenance_id              BIGINT REFERENCES dim_maintenance(maintenance_id),
#     temps_id                    BIGINT REFERENCES dim_temps(temps_id),
#     alarme_id                   BIGINT REFERENCES dim_alarme(alarme_id),
#     equipement_id               BIGINT REFERENCES dim_equipement(equipement_id),
#     puissance_w                 DECIMAL(15,2),
#     temperature_c               DECIMAL(15,2),
#     tension_v                   DECIMAL(15,2),
#     rayonnement                 DECIMAL(15,2),
#     courant_a                   DECIMAL(15,2),
#     preformance                 DECIMAL(15,2),
#     perte_production            DECIMAL(15,2),
#     ratio_vie_equipement        DECIMAL(15,2),
#     phase                       VARCHAR,
#     rayonnement_solaire_w_m2    DECIMAL(15,2),
#     temparature_ambiant_c       DECIMAL(15,2)
    
# )
# """)



#INSERTION

    con.execute(f"""
        INSERT INTO dim_equipement
        SELECT *
        FROM df_equip
        """)

    con.execute(f"""
            INSERT INTO dim_alarme (type_alarme, severite)
            SELECT DISTINCT type_alarme_id, severite
            FROM df_alarm
            """)

    con.execute(f"""
                INSERT INTO dim_temps (temps_id, date, annee, mois, jour, heure, minute)
    SELECT
        CAST(strftime(ts, '%Y%m%d%H%M') AS BIGINT) AS temps_id,
        CAST(ts AS DATE)                           AS date,
        year(ts), month(ts), day(ts), hour(ts), minute(ts)
    FROM (
        SELECT DISTINCT CAST(timestamp_telemetrie AS TIMESTAMP) AS ts
        FROM df_temps
    )
            """)

    con.execute(f"""
                INSERT INTO dim_maintenance (type_maintenance, duree)
                SELECT DISTINCT type_intervention, duree
                FROM df_maint
                ORDER BY type_intervention, duree
                """)


def main():
    
    df_DE, df_DA, df_DT, df_DM = lire_sqlite()
    creer_db(df_DE, df_DA, df_DT, df_DM)
    
    
if __name__ == "__main__":
    main()