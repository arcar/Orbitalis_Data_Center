import pandas as pd
from pathlib import Path
import sqlite3

CSV_PATH = "data/raw/equipements.csv"
OUTPUT_PATH = "data/equipements_propre.csv"
SITE_PATH = "data/raw/sites.csv"
SQL_DB = "data/raw/catalogue.db"
dossier_sortie = Path("data/lignes_rejetees")
dossier_sortie.mkdir(parents=True, exist_ok=True)
REJETS_PATH = "equipements_lignes_rejetees.csv"



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
    df["date_installation"] = pd.to_datetime(df["date_installation"], format="mixed", errors="coerce")
    df["date_installation"] = pd.to_datetime(df["date_installation"], format="ISO8601", utc=True, errors="coerce")
    
    # Dates manquantes ou invalides (NaT après conversion)
    idx_manquantes = df[df["date_installation"].isna()].index
    manquantes = df["date_installation"].isna()
    df_rejets_date_manquante = df[manquantes].copy()
    df_rejets_date_manquante["motif_rejet"] = "date manquante"
    print(len(idx_manquantes), "ligne(s) date manquante(s) supprimée(s)")
    df = df.drop(index=idx_manquantes)

    # Date dans le futur
    maintenant = pd.Timestamp.now(tz="UTC")
    futures = df[df["date_installation"] > maintenant].index
    futur_rejet = df["date_installation"] > maintenant
    df_rejets_date_futur = df[futur_rejet].copy()
    df_rejets_date_futur["motif_rejet"] = "date dans le futur"
    print(len(futures), "lignes date futur")
    df = df.drop(index=futures)

    
    print("\n4 : Suppression sans id_equipement valide")
    print("-" * 40)
    avant = len(df)
    id_equ = df["equipement_id"].str.match(r"^EQ-\d+$", case=False, na=False)
    df_rejets_id_eq = df[~id_equ].copy()
    df_rejets_id_eq["motif_rejet"] = "id_equipement invalide"
    df = df[id_equ].copy()
    apres = len(df)
    print((avant - apres), "ligne(s) supprimée(s) car Id_Equipement incorrect")


    print("\n5 : Valeurs puissance nominale négatives")
    print("-" * 40)
    puiss_negatif = df["puissance_nominale_w"] < 0
    print("nombre de valeurs négatives avant traitement :", (df["puissance_nominale_w"] < 0).sum())
    # Médiane de puissance_nominale_w, calculée par valeur de type (hors négatifs)
    mediane_par_type = (df.loc[~puiss_negatif].groupby("type")["puissance_nominale_w"].median())
    df.loc[puiss_negatif, "puissance_nominale_w"] = df.loc[puiss_negatif, "type"].map(mediane_par_type)   
    print("nombre de valeurs négatives apres traitement :", (df["puissance_nominale_w"] < 0).sum())


    # Modification des valeurs de "id_site"  pour s'assurer que l'on a les mêmes equipements que le fichier sites.csv
    print("\n6 : Cohérence id_site avec fichier sites.csv")
    print("-" * 40)
    nb_lignes_avant = len(df)
    print(f"Nombre de données : {nb_lignes_avant}")
    data_equip = pd.read_csv(SITE_PATH)
    ids_equipement = set(df["site_id"].dropna())
    ids_site = set(data_equip["site_id"].dropna())
    ids_inconnus = ids_equipement - ids_site
    lignes_inconnues = df[df["site_id"].isin(ids_inconnus)]
    if ids_inconnus:
        print("ERREUR : certains equipement_id de maintenance.csv n'existent pas dans sites.csv.")
        print("IDs inconnus :", sorted(ids_inconnus))
        # Nombre de lignes concernées par chaque equipement_id inconnu        
        print("Nombre de lignes par site_id inconnu :", lignes_inconnues["site_id"].value_counts())
        # Suppression des lignes
        nb_avant = len(df)
        id_equip_rejet = df["site_id"].isin(ids_inconnus)
        df_rejets_id_site = df[id_equip_rejet].copy()
        df_rejets_id_site["motif_rejet"] = "site_id inexistant"
        df = df[~df["site_id"].isin(ids_inconnus)]
        nb_supprimees = nb_avant - len(df)
        print(f"Nombre total de lignes supprimées : {nb_supprimees}")
    else:
        print("OK : tous les site_id de equipements.csv existent dans sites.csv.")
        df_rejets_id_site = df.iloc[0:0].copy()   # DataFrame vide avec les mêmes colonnes
        df_rejets_id_site["motif_rejet"] = pd.Series(dtype="object")
    
    nb_lignes_apres = len(df)
    print(f"Nombre de données après nettoyage - site_id - : {nb_lignes_apres}")


    print("\n7 : Supprimer equipement sans site")
    print("-" * 40)
    siteId_vide = df["site_id"].isna() | (df["site_id"].astype(str).str.strip() == "")
    df_rejets_vide = df[siteId_vide].copy()
    df_rejets_vide["motif_rejet"] = "siteId vide"
    df = df[~siteId_vide].copy()

    print("\n8 : Vérification et suppression doublons")
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



    df_rejets_total = pd.concat([df_rejets_doublons, df_rejets_vide, df_rejets_date_manquante, df_rejets_date_futur,df_rejets_id_eq, df_rejets_id_site], ignore_index=True)
    df_rejets_total.to_csv(dossier_sortie / REJETS_PATH, index=False)


    print("\n9 : Renommage des colonnes")
    print("-" * 40)
    df = df.rename(columns={"type": "type_equipement", "modele": "modele_id",})

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
