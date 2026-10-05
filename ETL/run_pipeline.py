from pathlib import Path
import pandas as pd
import sqlite3 

import etl_maintenance
import etl_sites
import etl_telemetrie
import etl_equipements
import etl_orbite
import etl_catalog
import etl_alarme

SQLITE_PATH = Path("./data/base_analytique.db")
SQL_DB = "data/raw/catalogue.db"

def creer_base(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS modele(
            modele_id              TEXT PRIMARY KEY,
            type_equipement        TEXT,
            fabricant              TEXT,
            puissance_nominale_w   INTEGER,
            rendement_nominal      REAL,
            duree_vie_annee        INTEGER,
            masse_kg               REAL
            )
        """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS site(
            site_id                TEXT PRIMARY KEY,
            nom                    TEXT,
            type_orbite            TEXT,
            altitude_km            INTEGER,
            inclinaison_deg        REAL,
            date_mise_en_service   TEXT,
            statut                 TEXT,
            description            TEXT,
            capacite_max_kw         INTEGER
            )
        """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mesures_orbite(
            mesure_id                   INTEGER PRIMARY KEY,
            timestamp_orbite            TEXT,
            phase                       TEXT,
            rayonnement_solaire_w_m2    REAL,
            temperature_ambiante_c      REAL,
            site_id                     TEXT NOT NULL,
            
            FOREIGN KEY (site_id) REFERENCES site (site_id)
            )
        """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS type_alarme(
            type_alarme_id              TEXT PRIMARY KEY,
            type_alarme                 TEXT,
            seuil_warning               REAL,
            seuil_critical              REAL,
            unite                       TEXT,
            description                 TEXT
            )
        """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS equipement(
            equipement_id               TEXT PRIMARY KEY,
            date_installation           TEXT,
            statut                      TEXT,
            site_id                     TEXT NOT NULL,
            modele_id                   TEXT NOT NULL,

            FOREIGN KEY (site_id) REFERENCES site (site_id),
            FOREIGN KEY (modele_id) REFERENCES modele (modele_id)
            )
        """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alarme(
            alarme_id                   TEXT PRIMARY KEY,
            timestamp_alarme            TEXT,
            severite                    TEXT,
            message                     TEXT,
            acquitee                    INTEGER,
            equipement_id               TEXT NOT NULL,
            type_alarme_id              TEXT NOT NULL,

            FOREIGN KEY (equipement_id) REFERENCES Equipement (equipement_id),
            FOREIGN KEY (type_alarme_id) REFERENCES type_alarme (type_alarme_id)
            )
        """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS maintenance(
            maintenance_id              TEXT PRIMARY KEY,
            date_debut                  TEXT,
            date_fin                    TEXT,
            technicien                  TEXT,
            type_intervention           TEXT,
            cout_eur                    INTEGER,
            commentaire                 TEXT,
            equipement_id               TEXT NOT NULL,

            FOREIGN KEY (equipement_id) REFERENCES Equipement (equipement_id)
            )
        """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telemetrie(
            telemetrie_id               TEXT PRIMARY KEY,
            timestamp_telemetrie        TEXT,
            puissance_w                 REAL,
            temperature_c               REAL,
            rayonnement                 REAL,
            tension_v                   REAL,
            courant_a                   REAL,
            equipement_id               TEXT NOT NULL,
  
            FOREIGN KEY (equipement_id) REFERENCES Equipement (equipement_id)
            )
        """)

def charger_base(modele, site):
    # Crée le dossier ./data s'il n'existe pas
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(SQLITE_PATH)

    try:
        cursor = conn.cursor()

        # Création des tables
        creer_base(cursor)

        # Insertion des données de la table "modele"
        for _, modele in modele.iterrows():
            cursor.execute(
                """
                INSERT INTO modele (
                    modele_id,
                    type_equipement,
                    fabricant,
                    puissance_nominale_w,
                    rendement_nominal,
                    duree_vie_annee,
                    masse_kg)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    modele["modele_id"],
                    modele["type_equipement"],
                    modele["fabricant"],
                    modele["puissance_nominale_w"],
                    modele["rendement_nominal"],
                    modele["duree_vie_annees"],
                    modele["masse_kg"],
                ),
            )
        # Insertion des données de la table "site"
        for _, site in site.iterrows():
            cursor.execute(
                """
                INSERT INTO site (
                    site_id,
                    nom,
                    type_orbite,
                    altitude_km,
                    inclinaison_deg,
                    date_mise_en_service,
                    statut,
                    description,
                    capacite_max_kw )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    site["site_id"],
                    site["nom"],
                    site["orbite_type"],
                    site["altitude_km"],
                    site["inclination_deg"],
                    site["date_mise_en_service"].isoformat(),
                    site["statut"],
                    site["description"],
                    site["capacite_max_kw"],
                ),
            )

        conn.commit()

    finally:
        conn.close()


def main():
    site = etl_sites.main()
    etl_equipements.main()
    etl_maintenance.main()
    etl_telemetrie.main()
    etl_orbite.main()
    etl_catalog.main()
    etl_alarme.main()

    cnx = sqlite3.connect(SQL_DB)
    modele = pd.read_sql("SELECT * FROM modeles", cnx)

    charger_base(modele, site)

if __name__ == "__main__":
    main()