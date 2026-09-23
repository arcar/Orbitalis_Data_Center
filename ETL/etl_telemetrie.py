import pandas as pd
from pathlib import Path
import sqlite3
from dateutil import parser as dtparser
from datetime import timezone

CSV_PATH = "data/raw/telemetrie.csv"
OUTPUT_PATH = "data/telemetrie_propre.csv"
EQUI_PATH = "data/raw/equipements.csv"
SQL_DB = "data/raw/catalogue.db"
dossier_sortie = Path("data/lignes_rejetees")
dossier_sortie.mkdir(parents=True, exist_ok=True)
REJETS_PATH = "telemetrie_lignes_rejetees.csv"



def lire_csv():
    df = pd.read_csv(CSV_PATH)
    return df

def parse_ts(s):
    dt = dtparser.parse(s, dayfirst=True)   # "01/05/2025" -> jour=01, mois=05
    if dt.tzinfo is None:
        # Aucune indication de fuseau -> on suppose que c'est déjà de l'UTC
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt

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
       
    df["timestamp"] = df["timestamp"].apply(parse_ts)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    # Format ISO 8601 UTC avec séparateur "T"
    df["timestamp"] = df["timestamp"].apply(lambda d: d.isoformat())
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True, errors="coerce")
    
    # Dates manquantes ou invalides (NaT après conversion)
    idx_manquantes = df[df["timestamp"].isna()].index
    manquantes = df["timestamp"].isna()
    df_rejets_date_manquante = df[manquantes].copy()
    df_rejets_date_manquante["motif_rejet"] = "date manquante"
    print(len(idx_manquantes), "ligne(s) date manquante(s) supprimée(s)")
    df = df.drop(index=idx_manquantes)

    # Date dans le futur
    maintenant = pd.Timestamp.now(tz="UTC")
    futures = df[df["timestamp"] > maintenant].index
    futur_rejet = df["timestamp"] > maintenant
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


    print("\n5 : Valeurs négatives/absentes et arrondi à 2 décimales")
    print("-" * 40)

    # Le timestamp a été stocké en texte ISO à l'étape 3 : on le reparse pour extraire l'heure
    ts = pd.to_datetime(df["timestamp"], utc=True)
    df["heure_creneau"] = ts.dt.strftime("%H:%M")

    # Colonnes où une valeur négative n'a pas de sens physique -> traitée comme invalide
    colonnes_sans_negatif = ["puissance_w", "rayonnement", "tension_v", "courant_a"]
    # Colonne où seule l'absence de valeur pose problème (le négatif est valide)
    colonnes_valeur_libre = ["temperature_c"]

    for colonne in colonnes_sans_negatif + colonnes_valeur_libre:
        if colonne in colonnes_sans_negatif:
            masque_invalide = (df[colonne] < 0) | (df[colonne].isna())
        else:
            masque_invalide = df[colonne].isna()

        print(f"{colonne} : {masque_invalide.sum()} valeur(s) invalide(s) avant traitement")

        moyenne_par_equip_heure = (
            df.loc[~masque_invalide]
              .groupby(["equipement_id", "heure_creneau"])[colonne]
              .mean()
              .reset_index()
              .rename(columns={colonne: f"{colonne}_moyenne"})
        )

        df = df.merge(moyenne_par_equip_heure, on=["equipement_id", "heure_creneau"], how="left")
        df.loc[masque_invalide, colonne] = df.loc[masque_invalide, f"{colonne}_moyenne"]
        df = df.drop(columns=[f"{colonne}_moyenne"])

        df[colonne] = df[colonne].round(2)

        if colonne in colonnes_sans_negatif:
            nb_apres = ((df[colonne] < 0) | df[colonne].isna()).sum()
        else:
            nb_apres = df[colonne].isna().sum()
        print(f"{colonne} : {nb_apres} valeur(s) invalide(s) après traitement")

    df = df.drop(columns=["heure_creneau"])


    # Modification des valeurs de "id_site"  pour s'assurer que l'on a les mêmes equipements que le fichier sites.csv
    print("\n6 : Cohérence equipement_id avec fichier equipements.csv")
    print("-" * 40)
    nb_lignes_avant = len(df)
    print(f"Nombre de données : {nb_lignes_avant}")
    data_equip = pd.read_csv(EQUI_PATH)
    ids_equipement = set(df["equipement_id"].dropna())
    ids_equi = set(data_equip["equipement_id"].dropna())
    ids_inconnus = ids_equipement - ids_equi
    lignes_inconnues = df[df["equipement_id"].isin(ids_inconnus)]
    if ids_inconnus:
        print("ERREUR : certains equipement_id de telemetrie.csv n'existent pas dans equipements.csv.")
        print("IDs inconnus :", sorted(ids_inconnus))
        # Nombre de lignes concernées par chaque equipement_id inconnu        
        print("Nombre de lignes par equipement_id inconnu :", lignes_inconnues["equipement_id"].value_counts())
        # Suppression des lignes
        nb_avant = len(df)
        id_equip_rejet = df["equipement_id"].isin(ids_inconnus)
        df_rejets_id_site = df[id_equip_rejet].copy()
        df_rejets_id_site["motif_rejet"] = "equipement_id inexistant"
        df = df[~df["site_id"].isin(ids_inconnus)]
        nb_supprimees = nb_avant - len(df)
        print(f"Nombre total de lignes supprimées : {nb_supprimees}")
    else:
        print("OK : tous les equipement_id de equipements.csv existent dans telemetrie.csv.")
        df_rejets_id_site = df.iloc[0:0].copy()   # DataFrame vide avec les mêmes colonnes
        df_rejets_id_site["motif_rejet"] = pd.Series(dtype="object")
    
    nb_lignes_apres = len(df)
    print(f"Nombre de données après nettoyage - site_id - : {nb_lignes_apres}")


    print("\n7 : Vérification et suppression doublons")
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



    df_rejets_total = pd.concat([df_rejets_doublons, df_rejets_date_manquante, df_rejets_date_futur,df_rejets_id_eq, df_rejets_id_site], ignore_index=True)
    df_rejets_total.to_csv(dossier_sortie / REJETS_PATH, index=False)

    
    valeurs_manquantes = df.isnull().sum().sum()
    print(f"\nTotal valeurs manquantes : {valeurs_manquantes}\n")
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nFichier nettoyé : '{OUTPUT_PATH}'")
    print(f"\nFichier lignes rejetées généré : '{REJETS_PATH}'")

    return df


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
