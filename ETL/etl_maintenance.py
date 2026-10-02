import pandas as pd
from pathlib import Path
import sqlite3

CSV_PATH = "data/raw/maintenance.csv"
OUTPUT_PATH = "data/maintenance_propre.csv"
EQUIP_PATH = "data/equipements_propre.csv"
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
    doublons = df.duplicated
    df_rejets_doublons = df[doublons].copy()
    df_rejets_doublons["motif_rejet"] = "doublon"
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
    df_rejets_date_incoherente = df[incoherentes].copy()
    df_rejets_date_incoherente["motif_rejet"] = "date incoherente"
    
    print(len(idx_incoherentes), "ligne(s) date incohérente(s) supprimée(s)")
    df = df.drop(index=idx_incoherentes)

    # Dates manquantes ou invalides (NaT après conversion)
    idx_manquantes = df[df["date_debut"].isna() | df["date_fin"].isna()].index
    manquantes = df["date_debut"].isna() | df["date_fin"].isna()
    df_rejets_date_manquante = df[manquantes].copy()
    df_rejets_date_manquante["motif_rejet"] = "date manquante"
    print(len(idx_manquantes), "ligne(s) date manquante(s) supprimée(s)")
    df = df.drop(index=idx_manquantes)

    # Date dans le futur
    maintenant = pd.Timestamp.now(tz="UTC")
    futures = df[df["date_debut"] > maintenant].index
    futur_rejet = df["date_debut"] > maintenant
    df_rejets_date_futur = df[futur_rejet].copy()
    df_rejets_date_futur["motif_rejet"] = "date dans le futur"
    print(len(futures), "lignes date futur")
    df = df.drop(index=futures)

    
    print("\n5 : Suppression sans id_equipement valide")
    print("-" * 40)
    avant = len(df)
    id_equ = df["equipement_id"].str.match(r"^EQ-\d+$", case=False, na=False)
    df_rejets_id_eq = df[~id_equ].copy()
    df_rejets_id_eq["motif_rejet"] = "id_equipement invalide"
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


    # Modification des valeurs de "equipement_id"  pour s'assurer que l'on a les mêmes equipements que le fichier equipements.csv
    print("\n7 : Cohérence id_equipement avec fichier equipements.csv")
    print("-" * 40)
    nb_lignes_avant = len(df)
    print(f"Nombre de données : {nb_lignes_avant}")
    data_equip = pd.read_csv(EQUIP_PATH)
    ids_maintenance = set(df["equipement_id"].dropna())
    ids_equipement = set(data_equip["equipement_id"].dropna())
    ids_inconnus = ids_maintenance - ids_equipement
    lignes_inconnues = df[df["equipement_id"].isin(ids_inconnus)]
    if ids_inconnus:
        print("ERREUR : certains equipement_id de maintenance.csv n'existent pas dans equipements.csv.")
        print("IDs inconnus :", sorted(ids_inconnus))
        # Nombre de lignes concernées par chaque equipement_id inconnu        
        print("Nombre de lignes par equipement_id inconnu :", lignes_inconnues["equipement_id"].value_counts())
        # Suppression des lignes
        nb_avant = len(df)
        id_equip_rejet = df["equipement_id"].isin(ids_inconnus)
        df_rejets_id_equip = df[id_equip_rejet].copy()
        df_rejets_id_equip["motif_rejet"] = "equipement_id inexistant"
        df = df[~df["equipement_id"].isin(ids_inconnus)]
        nb_supprimees = nb_avant - len(df)
        print(f"Nombre total de lignes supprimées : {nb_supprimees}")
    else:
        print("OK : tous les equipement_id de maintenance.csv existent dans equipements.csv.")
        df_rejets_id_equip = df.iloc[0:0].copy()   # DataFrame vide avec les mêmes colonnes
        df_rejets_id_equip["motif_rejet"] = pd.Series(dtype="object")
    
    nb_lignes_apres = len(df)
    print(f"Nombre de données après nettoyage - equipement_id - : {nb_lignes_apres}")



    df_rejets_total = pd.concat([df_rejets_doublons, df_rejets_date_incoherente, df_rejets_date_manquante, df_rejets_date_futur,df_rejets_id_eq, df_rejets_id_equip], ignore_index=True)
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
