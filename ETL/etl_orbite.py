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
    # Initialisation des DataFrames de rejets
    df_rejets_sites = pd.DataFrame()
    df_rejets_T_ensoleillement = pd.DataFrame()
    df_rejets_T_eclipse = pd.DataFrame()
    df_rejets_rayonnement = pd.DataFrame()
    df_rejets_doublons = pd.DataFrame()

    #---------------------------------------------------------------------------
    # Exploration des données
    #--------------------------------------------------------------------------- 
    print("=" * 40)
    print("1 : Vérification dtypes et non-null")
    print("=" * 40)
    df.info()
    nb_lignes_avant_nettoyage = len(df)

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
    print("3 : Modification format date")
    print("=" * 40)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", errors="coerce")
    print("Dates normalisées")


    #---------------------------------------------------------------------------
    # Vérification des valeurs aberrantes
    #---------------------------------------------------------------------------
    print("=" * 40)
    print("4 : Vérification des valeurs aberrantes")
    print("=" * 40)   

    # Modification des valeurs de "phase"  pour s'assurer que l'on a que les phases "ensoleillement" et "éclipse" autorisées
    print("--- PHASE ---")
    print("Valeurs uniques AVANT traitement :", df["phase"].unique())
    print("Nombre de valeurs uniques AVANT traitement :", df["phase"].nunique())
    
    valeurs_autorisees = ["ensoleillement", "eclipse"]
    df.loc[~df["phase"].isin(valeurs_autorisees), "phase"] = "inconnu"

    df.loc[(df["phase"] == "inconnu") & (df["rayonnement_solaire_w_m2"] > 0), "phase"] = "ensoleillement"
    df.loc[(df["phase"] == "inconnu") & (df["rayonnement_solaire_w_m2"] == 0), "phase"] = "eclipse"
    
    print("\nValeurs uniques APRES traitement :", df["phase"].unique())
    print("Nombre de valeurs uniques APRES traitement :", df["phase"].nunique())


    # Vérification de la cohérence des valeurs de "site_id"  pour s'assurer que l'on a les mêmes sites que le fichier sites.csv
    print("\n--- SITE ---")
    nb_lignes_avant = len(df)
    print(f"Nombre de données : {nb_lignes_avant}")
    
    data_site = pd.read_csv(SITE_PATH)

    ids_orbites = set(df["site_id"].dropna())
    ids_sites = set(data_site["site_id"].dropna())

    ids_inconnus = ids_orbites - ids_sites

    if ids_inconnus:
        print("ERREUR : certains site_id de orbites.csv n'existent pas dans sites.csv.")
        print("IDs inconnus :", sorted(ids_inconnus))

        # Nombre de lignes concernées par chaque site_id inconnu
        lignes_inconnues = df[df["site_id"].isin(ids_inconnus)]
        df_rejets_sites = lignes_inconnues.copy()
        df_rejets_sites["motif_rejet"] = "Site inconnu"

        print("\nNombre de lignes par site_id inconnu :")
        print(lignes_inconnues["site_id"].value_counts())

        # Suppression des lignes
        df = df[~df["site_id"].isin(ids_inconnus)]

        print(f"\nNombre total de lignes supprimées : {len(lignes_inconnues)}")

    else:
        print("OK : tous les site_id de orbites.csv existent dans sites.csv.")
    
    nb_lignes_apres = len(df)
    print(f"\nNombre de données après nettoyage - site_id - : {nb_lignes_apres}")


    # Vérification de la cohérence des valeurs de "temperature_ambiante_c"
    print("\n--- TEMPERATURE AMBIANTE ---")
    nb_lignes_avant = len(df)
    print(f"Nombre de données : {nb_lignes_avant}")

    # Identifier chaque cycle de phase
    df["cycle"] = (df["phase"] != df["phase"].shift()).cumsum()

    print("\nTempérature ensoleillement <-10 : ", ((df["phase"] == "ensoleillement") & (df["temperature_ambiante_c"] < -10)).sum())
    print("Température eclipse >=-10 : ", ((df["phase"] == "eclipse") & (df["temperature_ambiante_c"] >= -10)).sum())

    # Traitement de chaque cycle
    for cycle in df["cycle"].unique():
        lignes = df["cycle"] == cycle

        # Récupérer la phase du cycle
        phase = df.loc[lignes, "phase"].iloc[0]

        # Températures du cycle
        temperatures = df.loc[lignes, "temperature_ambiante_c"]

        # Garder uniquement les températures cohérentes
        if phase == "ensoleillement":
            temperatures_coherentes = temperatures[temperatures >= -10]

        elif phase == "eclipse":
            temperatures_coherentes = temperatures[temperatures < -10]

        else:
            continue

        # Calculer la médiane des températures cohérentes
        mediane = temperatures_coherentes.median()

        # Remplacer les valeurs manquantes par la médiane
        if pd.notna(mediane):
            df.loc[lignes & df["temperature_ambiante_c"].isna(), "temperature_ambiante_c"] = mediane

    # Identifier les températures incohérentes APRÈS remplacement des NaN
    anomalies_T_ensoleillement = ((df["phase"] == "ensoleillement") & (df["temperature_ambiante_c"] < -10))
    anomalies_T_eclipse = ((df["phase"] == "eclipse") & (df["temperature_ambiante_c"] >= -10))

    # Récupérer les lignes rejetées
    df_rejets_T_ensoleillement = df[anomalies_T_ensoleillement].copy()
    df_rejets_T_ensoleillement = df_rejets_T_ensoleillement.drop(columns="cycle")

    df_rejets_T_eclipse = df[anomalies_T_eclipse].copy()
    df_rejets_T_eclipse = df_rejets_T_eclipse.drop(columns="cycle")

    # Ajouter le motif de rejet
    df_rejets_T_ensoleillement["motif_rejet"] = "Température en phase d'ensoleillement incoherente"
    df_rejets_T_eclipse["motif_rejet"] = "Température en phase d'eclipse incoherente"

    # Supprimer les températures incohérentes du DataFrame principal
    df = df[~(anomalies_T_ensoleillement | anomalies_T_eclipse)].copy()

    # Supprimer la colonne temporaire
    df = df.drop(columns="cycle")


    print("\nRépartition des rejets température :")
    print(f"  Ensoleillement : {len(df_rejets_T_ensoleillement)}")
    print(f"  Eclipse : {len(df_rejets_T_eclipse)}")

    nb_lignes_apres = len(df)
    print(f"\nNombre de données après nettoyage - temperature_ambiante_c - : {nb_lignes_apres}")


    # Vérification de la cohérence des valeurs de "rayonnement_solaire_w_m2" 
    print("\n--- RAYONNEMENT SOLAIRE ---")
    nb_lignes_avant = len(df)
    print(f"Nombre de données : {nb_lignes_avant}")

    df["rayonnement_coherent"] = (((df["phase"] == "ensoleillement") & (df["rayonnement_solaire_w_m2"] > 0)) | ((df["phase"] == "eclipse") & (df["rayonnement_solaire_w_m2"] == 0)))
    
    erreurs = df[~df["rayonnement_coherent"]]
    df_rejets_rayonnement = erreurs.copy()
    df_rejets_rayonnement["motif_rejet"] = "Rayonnement incoherent"
    df_rejets_rayonnement = df_rejets_rayonnement.drop(columns="rayonnement_coherent")
    
    print(f"Il y a {len(erreurs)} ligne(s) avec un rayonnement incohérent --> suppression\n")
    print(erreurs)

    df = df[df["rayonnement_coherent"]]
    df = df.drop(columns="rayonnement_coherent")
    
    nb_lignes_apres = len(df)
    print(f"\nNombre de données après nettoyage - rayonnement_solaire_w_m2 - : {nb_lignes_apres}\n")

    #---------------------------------------------------------------------------
    # Vérification et suppression des doublons
    #---------------------------------------------------------------------------
    print("=" * 40)     
    print("5 : Vérification et suppression doublons")
    print("=" * 40) 
    nb_lignes_avant = len(df)
    print(f"\nNombre de données : {nb_lignes_avant}")
    doublons_count = df.duplicated().sum()
    print(f"\nNombre de doublons : {doublons_count}")
    doublons = df.duplicated()
    df_rejets_doublons = df[doublons].copy()
    df_rejets_doublons["motif_rejet"] = "doublon"
    if doublons_count > 0:
        df = df.drop_duplicates()
        nb_lignes_apres = len(df)
        print(f"\nDoublons supprimés, nouveau nombre de données : {nb_lignes_apres}\n")
    else : 
        nb_lignes_apres = len(df)

    
    #---------------------------------------------------------------------------
    # Création du fichier propre
    #---------------------------------------------------------------------------
    df_rejets_total = pd.concat([df_rejets_doublons, df_rejets_sites, df_rejets_T_ensoleillement, df_rejets_T_eclipse, df_rejets_rayonnement], ignore_index=True)
    df_rejets_total.to_csv(dossier_sortie / REJETS_PATH, index=False)

    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nFichier nettoyé : '{OUTPUT_PATH}'")

    nb_lignes_apres_nettoyage = len(df)
    
    #---------------------------------------------------------------------------
    # Récapitulatif
    #---------------------------------------------------------------------------
    print("\n--- RÉCAPITULATIF DES REJETS ---")
    print(f"Nombre de lignes avant nettoyage : {nb_lignes_avant_nettoyage}")
    print(f"Doublons : {len(df_rejets_doublons)}")
    print(f"Sites inconnus : {len(df_rejets_sites)}")
    print(f"Températures ensoleillement : {len(df_rejets_T_ensoleillement)}")
    print(f"Températures eclipse : {len(df_rejets_T_eclipse)}")
    print(f"Rayonnement : {len(df_rejets_rayonnement)}")
    print(f"Total rejets : {len(df_rejets_total)}")
    print(f"Nombre de lignes après nettoyage : {nb_lignes_apres_nettoyage}")

    return df


def main():
    df = lire_csv()
    df = nettoyage_csv(df)


if __name__ == "__main__":
    main()