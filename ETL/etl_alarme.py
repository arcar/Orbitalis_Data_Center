import json
import pandas as pd
from pathlib import Path
import sqlite3

json_str = '[{"nom": "Alice", "age": 25}, {"nom": "Bob", "age": 30}]'
data = json.loads(json_str)

# Transformer la liste de dictionnaires en DataFrame
df = pd.DataFrame(data)

JSON_PATH = "data/raw/alarmes.json"
OUTPUT_PATH = "data/alarmes_propre.csv"
SQL_DB = "data/raw/catalogue.db"
dossier_sortie = Path("data/lignes_rejetees")
dossier_sortie.mkdir(parents=True, exist_ok=True)
REJETS_PATH = "alarme_lignes_rejetees.csv"

def tranformer_dataframe(data):
    df = pd.DataFrame(data)
    return df

def lire_json():
    data = pd.read_json(JSON_PATH)
    df = tranformer_dataframe(data)
    return df

def nettoyage_json(df):
    # Initialisation des DataFrames de rejets
    df_rejets_alarmes = pd.DataFrame()
    df_rejets_equipement = pd.DataFrame()
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
    print("\n--- EQUIPEMENT ID ---")

    nb_lignes_avant = len(df)
    print(f"Nombre de données : {nb_lignes_avant}")

    masque_equipement_vide = (df["equipement_id"].isna() | df["equipement_id"].astype("string").str.strip().eq(""))

    print(f"Nombre de lignes avec equipement_id vide : {masque_equipement_vide.sum()}")

    # Extraction de l'Id equipement présent dans message
    equipement_depuis_message = (
        df.loc[masque_equipement_vide, "message"]
        .astype("string")
        .str.extract(
            r"détectée\s+sur\s+([A-Za-z0-9_-]+)",
            expand=False
        )
    )

    # Remplissage des identifiants des equipement_id vides
    df.loc[masque_equipement_vide, "equipement_id"] = (equipement_depuis_message)

    # Vérification des résultats
    print("\nEquipements récupérés depuis les messages :")

    print(
        df.loc[
            masque_equipement_vide,
            ["alarme_id", "equipement_id", "message"]
        ].to_string(index=False)
    )

    # Vérification s'il reste des equipement_id vides
    masque_equipement_toujours_vide = (df["equipement_id"].isna() | df["equipement_id"].astype("string").str.strip().eq(""))

    nb_equipements_toujours_vides = masque_equipement_toujours_vide.sum()

    if nb_equipements_toujours_vides > 0:

        print(f"\nATTENTION : {nb_equipements_toujours_vides} ligne(s) ont toujours un equipement_id vide.")

        print(
            df.loc[
                masque_equipement_toujours_vide,
                ["alarme_id", "equipement_id", "message"]
            ].to_string(index=False)
        )

        df_rejets_equipement = df.loc[masque_equipement_toujours_vide].copy()
        df_rejets_equipement["motif_rejet"] = "Equipement ID inconnu"


        df = df.loc[~masque_equipement_toujours_vide].copy()

    else:
        print("\nOK : tous les equipement_id sont renseignés.")

    nb_lignes_apres = len(df)
    print(f"\nNombre de données après vérification - equipement_id - : {nb_lignes_apres}")


    # Vérification de la cohérence des valeurs de "type_alarme" pour s'assurer que l'on a les mêmes type d'alarme que la table seuils_alarmes
    print("\n--- TYPE ALARME ---")
    nb_lignes_avant = len(df)
    print(f"Nombre de données : {nb_lignes_avant}")

    conn = sqlite3.connect(SQL_DB)
    df_ref = pd.read_sql("SELECT type_alarme FROM seuils_alarmes", conn)

    type_alarme = set(df["type_alarme"].dropna())
    ref_type_alarme = set(df_ref["type_alarme"].dropna())

    types_inconnus = type_alarme - ref_type_alarme

    if types_inconnus:
        print("ATTENTION : certains types d'alarmes de alarmes.json n'existent pas dans la base de données.")
        print("Types inconnus :", sorted(types_inconnus))

        # Lignes concernées
        lignes_inconnues = df[df["type_alarme"].isin(types_inconnus)].copy()

        print("\nNombre de lignes par type_alarme inconnu :")
        print(lignes_inconnues["type_alarme"].value_counts())

        # Remplacement du type_alarme à partir de la colonne message
        masque_inconnu = df["type_alarme"].isin(types_inconnus)

        type_depuis_message = (
            df.loc[masque_inconnu, "message"]
            .astype("string")
            .str.extract(r"Alerte\s+(.+?)\s+détectée", expand=False)
        )

        # Conserver UNKNOWN_TYPE si on n'arrive pas à extraire le type depuis le message,
        type_depuis_message = type_depuis_message.fillna("UNKNOWN_TYPE")

        # Remplacer les types inconnus
        df.loc[masque_inconnu, "type_alarme"] = type_depuis_message

        print("\nTypes d'alarmes après analyse du message :")
        print(df.loc[masque_inconnu, "type_alarme"].value_counts())

    else:
        print("OK : tous les types d'alarmes de alarmes.json existent dans la base de données catalogue.db.")


    types_apres_extraction = set(df.loc[masque_inconnu, "type_alarme"].dropna())

    # Deuxième vérification des types inconnue après extraction
    types_toujours_inconnus = types_apres_extraction - ref_type_alarme

    if types_toujours_inconnus:
        print("\nTypes toujours inconnus après extraction du message :")
        print(sorted(types_toujours_inconnus))

        # Sauvegarde des lignes rejetées/inconnues si nécessaire
        lignes_toujours_inconnues = df[df["type_alarme"].isin(types_toujours_inconnus)].copy()
        df_rejets_alarmes = lignes_toujours_inconnues.copy()
        df_rejets_alarmes["motif_rejet"] = "Type d'alarme inconnu"

        df = df[~df["type_alarme"].isin(types_toujours_inconnus)]

    nb_lignes_apres = len(df)

    print(f"\nNombre de données après nettoyage - type_alarme - : {nb_lignes_apres}")

    #---------------------------------------------------------------------------
    # Vérification et suppression des doublons
    #---------------------------------------------------------------------------
    print("=" * 40)     
    print("5 : Vérification et suppression doublons")
    print("=" * 40) 
    nb_lignes_avant = len(df)
    print(f"\nNombre de données : {nb_lignes_avant}")
    doublons_count = df.duplicated(subset = ["alarme_id"]).sum()
    print(f"\nNombre de doublons : {doublons_count}")
    doublons = df.duplicated(subset = ["alarme_id"])
    df_rejets_doublons = df[doublons].copy()
    df_rejets_doublons["motif_rejet"] = "doublon"
    if doublons_count > 0:
        df = df.drop_duplicates(subset = ["alarme_id"], keep = "first")
        nb_lignes_apres = len(df)
        print(f"\nDoublons supprimés, nouveau nombre de données : {nb_lignes_apres}\n")
    else : 
        nb_lignes_apres = len(df)

    #---------------------------------------------------------------------------
    # Création du fichier propre
    #---------------------------------------------------------------------------
    df_rejets_total = pd.concat([df_rejets_doublons, df_rejets_alarmes, df_rejets_equipement], ignore_index=True)
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
    print(f"Equipement inconnu : {len(df_rejets_equipement)}")
    print(f"Type d'alarme inconnu : {len(df_rejets_alarmes)}")
    print(f"Nombre de lignes après nettoyage : {nb_lignes_apres_nettoyage}")    

def main():
    df = lire_json()
    df = nettoyage_json(df)


if __name__ == "__main__":
    main()