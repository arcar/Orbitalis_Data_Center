import pandas as pd
from pathlib import Path
import sqlite3

CSV_PATH = "data/raw/maintenance.csv"
OUTPUT_PATH = "data/maintenance_propre.csv"
SQL_DB = "data/raw/catalogue.db"
dossier_sortie = Path("data/lignes_rejetees")
dossier_sortie.mkdir(parents=True, exist_ok=True)
REJETS_PATH = "maintenance_lignes_rejetees.csv"



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

    print("\n3 : Vérification et suppression doublons")
    print("-" * 40)
    nb_lignes_avant = len(df)
    print(f"\nNombre de données : {nb_lignes_avant}")
    doublons_count = df.duplicated().sum()
    print(f"\nNombre de doublons : {doublons_count}")
    if doublons_count > 0:
        df = df.drop_duplicates()
        nb_lignes_apres = len(df)
        print(f"\nDoublons supprimés, nouveau nombre de données : {nb_lignes_apres}\n")
    else : 
        nb_lignes_apres = len(df)


    print("\n4 : Modification format et coherence date")
    print("-" * 40)
    df["date_debut"] = pd.to_datetime(df["date_debut"], format="mixed", errors="coerce")
    df["date_fin"] = pd.to_datetime(df["date_fin"], format="mixed", errors="coerce")
    df["date_debut"] = pd.to_datetime(df["date_debut"], format="ISO8601", utc=True, errors="coerce")
    df["date_fin"] = pd.to_datetime(df["date_fin"], format="ISO8601", utc=True, errors="coerce")

    # Lignes incohérentes : fin avant début
    idx_incoherentes = df[df["date_fin"] < df["date_debut"]].index
    incoherentes = df["date_fin"] < df["date_debut"]
    df_rejets = df[incoherentes].copy()
    df_rejets.to_csv(dossier_sortie /REJETS_PATH, index = False)
    print(len(idx_incoherentes), "ligne(s) date incohérente(s) supprimée(s)")
    df = df.drop(index=idx_incoherentes)

    # Dates manquantes ou invalides (NaT après conversion)
    manquantes = df[df["date_debut"].isna() | df["date_fin"].isna()].index
    print(len(manquantes), "ligne(s) date manquante(s) supprimée(s)")
    df = df.drop(index=manquantes)

    # Date dans le futur
    maintenant = pd.Timestamp.now(tz="UTC")
    futures = df[df["date_debut"] > maintenant]
    print(len(futures), "lignes date futur")

    
    print("\n5 : Suppression sans id_equipement valide")
    print("-" * 40)
    avant = len(df)
    id_equ = df["equipement_id"].str.match(r"^EQ-\d+$", case=False, na=False)
    df = df[id_equ].copy()
    apres = len(df)
    print((avant - apres), "ligne(s) supprimée(s) car Id_Equipement incorrect")

    print("\n6 : Valeurs cout_eur négatives")
    print("-" * 40)
    cout_negatif = df["cout_eur"] < 0
    print("nombre de valeurs négatives avant traitement :", (df["cout_eur"] < 0).sum())
    # Médiane de cout_eur, calculée par valeur d'intervention (hors négatifs)
    mediane_par_intervention = (df.loc[~cout_negatif].groupby("type_intervention")["cout_eur"].median())
    df.loc[cout_negatif, "cout_eur"] = df.loc[cout_negatif, "type_intervention"].map(mediane_par_intervention)   
    print("nombre de valeurs négatives apres traitement :", (df["cout_eur"] < 0).sum())



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
