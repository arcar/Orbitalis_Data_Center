import pandas as pd
from pathlib import Path
import sqlite3

CSV_PATH = "data/raw/sites.csv"
OUTPUT_PATH = "data/sites_propre.csv"
SQL_DB = "data/raw/catalogue.db"
dossier_sortie = Path("data/lignes_rejetees")
dossier_sortie.mkdir(parents=True, exist_ok=True)
REJETS_PATH = "sites_lignes_rejetees.csv"



def lire_csv():
    df = pd.read_csv(CSV_PATH)
    return df

def nettoyage_csv(df):

    print("\n1 : Vérification dtypes et non-null")
    print("-" * 40)
    df.info()


    print("\n2 : Vérification si valeurs manquantes")
    print("-" * 40)
    print(df.isnull().sum())
    valeurs_manquantes = df.isnull().sum().sum()
    print(f"\nTotal valeurs manquantes : {valeurs_manquantes}\n")


    print("\n3 : Modification format et coherence date")
    print("-" * 40)
    df["date_mise_en_service"] = pd.to_datetime(df["date_mise_en_service"], format="mixed", errors="coerce")
    df["date_mise_en_service"] = pd.to_datetime(df["date_mise_en_service"], format="ISO8601", utc=True, errors="coerce")
    
    # Dates manquantes ou invalides (NaT après conversion)
    idx_manquantes = df[df["date_mise_en_service"].isna()].index
    manquantes = df["date_mise_en_service"].isna()
    df_rejets_date_manquante = df[manquantes].copy()
    df_rejets_date_manquante["motif_rejet"] = "date manquante"
    print(len(idx_manquantes), "ligne(s) date manquante(s) supprimée(s)")
    df = df.drop(index=idx_manquantes)

    # Date dans le futur
    maintenant = pd.Timestamp.now(tz="UTC")
    futures = df[df["date_mise_en_service"] > maintenant].index
    futur_rejet = df["date_mise_en_service"] > maintenant
    df_rejets_date_futur = df[futur_rejet].copy()
    df_rejets_date_futur["motif_rejet"] = "date dans le futur"
    print(len(futures), "lignes date futur")
    df = df.drop(index=futures)


    print("\n4 : Recuperer altitude dans db")
    print("-" * 40)
    conn = sqlite3.connect(SQL_DB)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(references_sites)")
    for col in cursor.fetchall():
        print(col)
    df_ref = pd.read_sql("SELECT site_id, zone_orbitale FROM references_sites", conn)
    conn.close()
    # Extrait le nombre juste avant "km" : "LEO-SSO 550km" -> 550.0
    df_ref["altitude_km_ref"] = (df_ref["zone_orbitale"].str.extract(r"(\d+(?:\.\d+)?)\s*km", expand=False).astype(float))
    df = df.merge(df_ref[["site_id", "altitude_km_ref"]], on="site_id", how="left")
    # On ne remplace que les cases vides d'altitude_km
    df["altitude_km"] = df["altitude_km"].fillna(df["altitude_km_ref"])
    df = df.drop(columns=["altitude_km_ref"])
    encore_vide = df[df["altitude_km"].isna()]
    if not encore_vide.empty:
        print("Toujours vide (site_id introuvable ou format non reconnu dans zone_orbitale) :")
        print(encore_vide[["site_id", "nom"]])
    else:
        print("OK : toutes les altitudes ont été complétées.")
    print(df[["site_id", "nom", "altitude_km"]])


    
    print("\n5 : Vérification et suppression doublons")
    print("-" * 40)
    nb_lignes_avant = len(df)
    print(f"\nNombre de données : {nb_lignes_avant}")
    doublons_count = df.duplicated().sum()
    print(f"\nNombre de doublons : {doublons_count}")
    doublons = df.duplicated
    df_rejets_doublons = df[doublons].copy()
    df_rejets_doublons["motif_rejet"] = "doublon"
    if doublons_count > 0:
        df = df.drop_duplicates()
        nb_lignes_apres = len(df)
        print(f"\nDoublons supprimés, nouveau nombre de données : {nb_lignes_apres}\n")
    else : 
        nb_lignes_apres = len(df)



    df_rejets_total = pd.concat([df_rejets_doublons, df_rejets_date_manquante, df_rejets_date_futur], ignore_index=True)
    df_rejets_total.to_csv(dossier_sortie / REJETS_PATH, index=False)


    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nFichier nettoyé : '{OUTPUT_PATH}'")
    print(f"\nFichier lignes rejetées généré : '{REJETS_PATH}'")

    return df


# def creer_db (chemin_db = SQL_DB):
#     connexion = sqlite3.connect(chemin_db)
#     connexion.execute("""
#         CREATE TABLE IF NOT EXISTS solaire (
#             id_solar              INTEGER PRIMARY KEY AUTOINCREMENT,
#             date                  DATE,
#             start_hour            INTEGER,
#             end_hour              INTEGER,
#             day_of_year           INTEGER,
#             day_name              TEXT,
#             month_name            TEXT,
#             season                TEXT,
#             production            INTEGER
#         )
#     """)
#     connexion.commit()
#     connexion.close()


# def alimenter_solaire(df, chemin_db=SQL_DB):
    
#     connexion = sqlite3.connect(chemin_db)

#     df_a_inserer = df.copy()
#     df_a_inserer["Date"] = df_a_inserer["Date"].dt.strftime("%Y-%m-%d")
#     df_a_inserer.to_sql("solaire", connexion, if_exists="replace", index=False)

#     connexion.close()
#     print(f"  {len(df)} lignes insérées dans db_solaire")

# def executer_SQL(chemin_db=SQL_DB):
#     connexion = sqlite3.connect(chemin_db)

#     requetes = {
#         "Nombre de relevés par saison": """
#             SELECT season, COUNT(*) AS nb_releves
#             FROM solaire GROUP BY season ORDER BY nb_releves DESC
#         """,
#         "Production moyenne par saison": """
#             SELECT season, ROUND(AVG(production), 2) AS production_moyenne
#             FROM solaire GROUP BY season ORDER BY production_moyenne DESC
#         """,
#         "Production moyenne par heure": """
#             SELECT start_hour, end_hour, ROUND(AVG(production), 2) AS production_moyenne
#             FROM solaire GROUP BY start_hour ORDER BY start_hour
#         """,
#         "Top 10 jours de production": """
#             SELECT date, production FROM solaire
#             ORDER BY production DESC LIMIT 10
#         """,
#         "Production moyenne par jour de semaine": """
#             SELECT day_name, ROUND(AVG(production), 2) AS production_moyenne
#             FROM solaire GROUP BY day_name ORDER BY production_moyenne DESC
#         """,
#     }

#     for titre, requete in requetes.items():
#         print(f"\n{titre}")
#         print("-" * 40)
#         resultat = pd.read_sql(requete, connexion)
#         print(resultat.to_string(index=False))

#     connexion.close()

def main():
    df = lire_csv()
    df = nettoyage_csv(df)
    # creer_db()
    # alimenter_solaire(df)
    # executer_SQL()
    # df_sql = charger_donnees()
    # graphique_production_temporelle(df_sql)
    # graphique_production_par_heure(df_sql)
    # modele, y_test, y_pred, dates_test = entrainer_modele(df_sql)
    # entrainer_modele_groupe(df_sql)
    # baseline_moyenne_groupe(df_sql)
    # graphique_predictions(dates_test, y_test, y_pred)

if __name__ == "__main__":
    main()
