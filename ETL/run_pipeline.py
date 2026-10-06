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
            mesure_id                   INTEGER PRIMARY KEY AUTOINCREMENT,
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
            type_alarme_id              INTEGER PRIMARY KEY AUTOINCREMENT,
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

def charger_base(modele, site, mesures_orbite, type_alarme, equipement):
    # Crée le dossier ./data s'il n'existe pas
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(SQLITE_PATH)

    try:
        cursor = conn.cursor()

        # Création des tables
        creer_base(cursor)

        # Insertion des données de la table "modele"
        for _, row in modele.iterrows():
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
                    row["modele_id"],
                    row["type_equipement"],
                    row["fabricant"],
                    row["puissance_nominale_w"],
                    row["rendement_nominal"],
                    row["duree_vie_annees"],
                    row["masse_kg"],
                ),
            )
        # Insertion des données de la table "site"
        for _, row in site.iterrows():
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
                    row["site_id"],
                    row["nom"],
                    row["orbite_type"],
                    row["altitude_km"],
                    row["inclination_deg"],
                    row["date_mise_en_service"].isoformat(),
                    row["statut"],
                    row["description"],
                    row["capacite_max_kw"],
                ),
            )

        # Insertion des données de la table "mesures_orbite"
        for _, row in mesures_orbite.iterrows():
            cursor.execute(
                """
                INSERT INTO mesures_orbite (
                    timestamp_orbite,
                    phase,
                    rayonnement_solaire_w_m2,
                    temperature_ambiante_c,
                    site_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["timestamp"].isoformat(),
                    row["phase"],
                    row["rayonnement_solaire_w_m2"],
                    row["temperature_ambiante_c"],
                    row["site_id"],
                ),
            )

        # Insertion des données de la table "type_alarme"
        for _, row in type_alarme.iterrows():
            cursor.execute(
                """
                INSERT INTO type_alarme (
                    type_alarme,
                    seuil_warning,
                    seuil_critical,
                    unite,
                    description)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["type_alarme"],
                    row["seuil_warning"],
                    row["seuil_critical"],
                    row["unite"],
                    row["description"],
                ),
            )  

        # Insertion des données de la table "equipement"
        for _, row in equipement.iterrows():
            cursor.execute(
                """
                INSERT INTO equipement (
                    equipement_id,
                    date_installation,
                    statut,
                    site_id,
                    modele_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["equipement_id"],
                    row["date_installation"].isoformat(),
                    row["statut"],
                    row["site_id"],
                    row["modele_id"],
                ),
            )

        conn.commit()

    finally:
        conn.close()


def main():
    site = etl_sites.main()
    equipement = etl_equipements.main()
    etl_maintenance.main()
    etl_telemetrie.main()
    mesures_orbite = etl_orbite.main()
    etl_catalog.main()
    etl_alarme.main()

    cnx = sqlite3.connect(SQL_DB)
    modele = pd.read_sql("SELECT * FROM modeles", cnx)
    type_alarme = pd.read_sql("SELECT * FROM seuils_alarmes", cnx)

    charger_base(modele, site, mesures_orbite, type_alarme, equipement)

if __name__ == "__main__":
    main()