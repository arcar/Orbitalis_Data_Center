import pandas as pd
from pathlib import Path

CSV_PATH = "data/raw/orbite.csv"
SITE_PATH = "data/raw/sites.csv"

OUTPUT_PATH = "data/orbite_propre.csv"

dossier_sortie = Path("data/lignes_rejetees")
dossier_sortie.mkdir(parents=True, exist_ok=True)
REJETS_PATH = "orbite_lignes_rejetees.csv"


def lire_csv():
    df = pd.read_csv(CSV_PATH)
    return df

def nettoyage_csv(df):
    #---------------------------------------------------------------------------
    # Exploration des données
    #--------------------------------------------------------------------------- 
    print("=" * 40)
    print("1 : Vérification dtypes et non-null")
    print("=" * 40)
    df.info()

     #---------------------------------------------------------------------------
    # Vérification des valeurs manquantes
    #---------------------------------------------------------------------------  
    print("=" * 40)
    print("2 : Vérification si valeurs manquantes")
    print("=" * 40)
    print(df.isnull().sum())
    valeurs_manquantes = df.isnull().sum().sum()
    print(f"\nTotal valeurs manquantes : {valeurs_manquantes}\n")

    #---------------------------------------------------------------------------
    # Normalisation de la date
    #---------------------------------------------------------------------------
    print("=" * 40)
    print("4 : Modification format date")
    print("=" * 40)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", errors="coerce")
    print("Dates normalisées")


    #---------------------------------------------------------------------------
    # Vérification des valeurs aberrantes
    #---------------------------------------------------------------------------
    print("=" * 40)
    print("5 : Vérification des valeurs aberrantes")
    print("=" * 40)   
    # Modification des valeurs de "phase"  pour s'assurer que l'on a que les phases "ensoleillement" et "éclipse" autorisées
    print("--- phase ---")
    print("Valeurs uniques AVANT traitement :", df["phase"].unique())
    print("Nombre de valeurs uniques AVANT traitement :", df["phase"].nunique())
    valeurs_autorisees = ["ensoleillement", "eclipse"]
    df.loc[~df["phase"].isin(valeurs_autorisees), "phase"] = "inconnu"

    df.loc[(df["phase"] == "inconnu") & (df["rayonnement_solaire_w_m2"] > 0), "phase"] = "ensoleillement"
    df.loc[(df["phase"] == "inconnu") & (df["rayonnement_solaire_w_m2"] == 0), "phase"] = "eclipse"
    
    print("\nValeurs uniques APRES traitement :", df["phase"].unique())
    print("Nombre de valeurs uniques APRES traitement :", df["phase"].nunique())

    # Modification des valeurs de "site_id"  pour s'assurer que l'on a les mêmes sites que le fichier sites.csv
    print("\n--- site_id ---")
    nb_lignes_avant = len(df)
    print(f"\nNombre de données : {nb_lignes_avant}")
    
    data_site = pd.read_csv(SITE_PATH)

    ids_orbites = set(df["site_id"].dropna())
    ids_sites = set(data_site["site_id"].dropna())

    ids_inconnus = ids_orbites - ids_sites

    if ids_inconnus:
        print("ERREUR : certains site_id de orbites.csv n'existent pas dans sites.csv.")
        print("IDs inconnus :", sorted(ids_inconnus))

        # Nombre de lignes concernées par chaque site_id inconnu
        lignes_inconnues = df[df["site_id"].isin(ids_inconnus)]

        print("\nNombre de lignes par site_id inconnu :")
        print(lignes_inconnues["site_id"].value_counts())

        # Suppression des lignes
        nb_avant = len(df)

        df = df[~df["site_id"].isin(ids_inconnus)]

        nb_supprimees = nb_avant - len(df)

        print(f"\nNombre total de lignes supprimées : {nb_supprimees}")

    else:
        print("OK : tous les site_id de orbites.csv existent dans sites.csv.")
    
    nb_lignes_apres = len(df)
    print(f"\nNombre de données après nettoyage - site_id - : {nb_lignes_apres}")


    # Modification des valeurs de "site_id"  pour s'assurer que l'on a les mêmes sites que le fichier sites.csv
    print("\n--- rayonnement_solaire_w_m2 ---")
    
    nb_lignes_avant = len(df)
    print(f"\nNombre de données : {nb_lignes_avant}")

    df["rayonnement_coherent"] = (((df["phase"] == "ensoleillement") & (df["rayonnement_solaire_w_m2"] > 0)) | ((df["phase"] == "eclipse") & (df["rayonnement_solaire_w_m2"] == 0)))
    
    erreurs = df[~df["rayonnement_coherent"]]
    print(len(erreurs))
    df_rejets = erreurs.copy()
    df_rejets["commentaire"] = "Suppresionn pour rayonnement incoherent"
    df_rejets.to_csv(dossier_sortie /REJETS_PATH, index = False)
    
    print(len(erreurs), "ligne(s) avec un rayonnement incohérent supprimée(s)")

    df = df[df["rayonnement_coherent"]]
    
    nb_lignes_apres = len(df)
    print(f"\nNombre de données après nettoyage - rayonnement_solaire_w_m2 - : {nb_lignes_apres}")

    print(erreurs)


    #---------------------------------------------------------------------------
    # Vérification et suppression des doublons
    #---------------------------------------------------------------------------
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
    
    #---------------------------------------------------------------------------
    # Création du fichier propre
    #---------------------------------------------------------------------------    
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nFichier nettoyé : '{OUTPUT_PATH}'")

    return df


def main():
    df = lire_csv()
    df = nettoyage_csv(df)


main()