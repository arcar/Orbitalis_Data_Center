import pandas as pd
import numpy as np
from pathlib import Path
from dateutil import parser as dtparser
from datetime import timezone
import re

CSV_PATH = "data/raw/telemetrie.csv"
OUTPUT_PATH = "data/telemetrie_propre.csv"
EQUI_PATH = "data/equipements_propre.csv"
SQL_DB = "data/raw/catalogue.db"
dossier_sortie = Path("data/lignes_rejetees")
dossier_sortie.mkdir(parents=True, exist_ok=True)
REJETS_PATH = "telemetrie_lignes_rejetees.csv"
ENV_PATH = "data/orbite_propre.csv"


def lire_csv():
    df = pd.read_csv(CSV_PATH)
    return df

def parse_ts(s):
    if pd.isna(s):
        return pd.NaT

    s = str(s).strip()

    # On teste d'abord les formats connus
    formats = [
        "%d/%m/%Y %H:%M",          # 01/05/2025 00:00
        "%Y-%m-%d %H:%M:%S.%fZ",   # 2025-05-01 00:05:00.000Z
        "%Y-%m-%d %H:%M:%S",       # 2025-05-01 00:05:00
        "%Y/%m/%d %H:%M",          # 2025/05/01 00:10
        "%Y-%m-%dT%H:%M:%S",       # 2025-05-01T00:15:00
    ]

    for fmt in formats:
        try:
            return pd.to_datetime(
                s, format=fmt, utc=True, errors="raise"
            )
        except (ValueError, TypeError):
            pass

    return pd.NaT

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
        df = df[~id_equip_rejet].copy()
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
    nb_avant = len(df)
    doublons_cle = df.duplicated(subset=["equipement_id", "timestamp"], keep="first")
    df_rejets_doublons = df[doublons_cle].copy()
    df_rejets_doublons["motif_rejet"] = "doublon equipement/timestamp"
    df = df[~doublons_cle].copy()
    print(f"{doublons_cle.sum()} doublon(s) supprimé(s) ({nb_avant} -> {len(df)} lignes)")

# ============================================================
# # 8 : PHASES, VALEURS ABERRANTES ET IMPUTATION
# ============================================================

    print("\n8 : Correction des valeurs par séquence de phase")
    print("-" * 60)

    # ------------------------------------------------------------
    # 8.1 : Récupération des informations des équipements
    # ------------------------------------------------------------

    df = df.merge(
        data_equip[
            [
                "equipement_id",
                "site_id",
                "puissance_nominale_w",
            ]
        ].drop_duplicates(subset=["equipement_id"]),
        on="equipement_id",
        how="left",
        validate="many_to_one",
    )

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        utc=True,
        errors="coerce",
    )

    # ------------------------------------------------------------
    # 8.2 : Récupération de la phase environnementale
    # ------------------------------------------------------------

    env = pd.read_csv(ENV_PATH)

    # Conversion explicite des timestamps en UTC
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    env["timestamp"] = pd.to_datetime(env["timestamp"], utc=True, errors="coerce")

    # Normalisation des identifiants
    df["site_id"] = df["site_id"].astype("string").str.strip()
    env["site_id"] = env["site_id"].astype("string").str.strip()

    env["phase"] = (env["phase"].astype("string").str.strip().str.lower())

    # Éliminer les lignes environnementales inutilisables
    env = env.dropna(subset=["timestamp", "site_id", "phase"]).copy()

    env = env[env["phase"].isin(["ensoleillement", "eclipse"])].copy()

    # Vérifier les phases contradictoires à un même instant et site
    conflits = (env.groupby(["site_id", "timestamp"])["phase"].nunique())

    if (conflits > 1).any():
        raise ValueError("Phases contradictoires pour un même site et timestamp.")

    env = env.drop_duplicates(subset=["site_id", "timestamp"])

    # Trier globalement sur le timestamp : c'est indispensable
    # pour merge_asof.
    gauche = (
        df.drop(columns=["phase"], errors="ignore")
        .dropna(subset=["timestamp", "site_id"])
        .sort_values("timestamp")
        .copy()
    )

    droite = (
        env[["timestamp", "site_id", "phase"]]
        .sort_values("timestamp")
        .copy()
    )

    df = pd.merge_asof(
        gauche,
        droite,
        on="timestamp",
        by="site_id",
        direction="backward",
        tolerance=pd.Timedelta("15min"),
    )

    print(
        "Mesures sans phase après jointure :",
        int(df["phase"].isna().sum()),
    )

    print("\nRépartition des phases :")
    print(df["phase"].value_counts(dropna=False))

# ------------------------------------------------------------
# 8.2 bis : Récupération des phases manquantes par la température
# ------------------------------------------------------------

    # Compter les phases manquantes avant correction
    masque_phase_manquante = df["phase"].isna()
    nb_phases_manquantes = int(masque_phase_manquante.sum())

    print("\nPhases manquantes avant correction :", nb_phases_manquantes,)

    # S'assurer que la température est numérique
    df["temperature_c"] = pd.to_numeric(
        df["temperature_c"],
        errors="coerce",
    )

    # Inférer la phase uniquement si la jointure n'a rien trouvé
    df.loc[
        masque_phase_manquante
        & (df["temperature_c"] >= 0),
        "phase",
    ] = "ensoleillement"

    df.loc[
        masque_phase_manquante
        & (df["temperature_c"] < 0),
        "phase",
    ] = "eclipse"

    # Vérifier le résultat
    print(
        "Phases déduites de la température :",
        int(
            (
                masque_phase_manquante
                & df["temperature_c"].notna()
            ).sum()
        ),
    )

    print(
        "Phases encore manquantes :",
        int(df["phase"].isna().sum()),
    )

    print("\nRépartition des phases après correction :")
    print(df["phase"].value_counts(dropna=False))

    # ------------------------------------------------------------
    # 8.3 : Identification des séquences continues de phase
    # ------------------------------------------------------------

    # Une nouvelle séquence commence lorsque la phase change.
    # Une phase inconnue constitue également une séparation :
    # on ne mélange pas les valeurs de part et d'autre.

   # Trier les données avant de rechercher les changements de phase.
    df = df.sort_values(
        ["equipement_id", "timestamp"]
    ).reset_index(drop=True)

    # Convertir la phase en chaîne Python classique.
    # Cela évite les problèmes de conversion liés à string[pyarrow].
    phase_bloc = (
        df["phase"]
        .astype(object)
        .where(df["phase"].notna(), "__PHASE_INCONNUE__")
    )

    # Phase précédente pour chaque équipement.
    phase_precedente = (
        phase_bloc.groupby(df["equipement_id"], sort=False)
        .shift()
    )

    # Nouvelle séquence :
    # 1. première mesure de l'équipement ;
    # 2. changement de phase ;
    # 3. passage vers ou depuis une phase inconnue.
    nouveau_bloc = (
        phase_precedente.isna()
        | phase_bloc.ne(phase_precedente)
    )

    # Convertir explicitement en booléens NumPy sans valeur manquante.
    nouveau_bloc = nouveau_bloc.fillna(True).to_numpy(dtype=bool)

    # Numéro de séquence indépendant pour chaque équipement.
    df["bloc_phase"] = (
        pd.Series(nouveau_bloc, index=df.index)
        .astype("int64")
        .groupby(df["equipement_id"], sort=False)
        .cumsum()
    )

    # Une mesure sans phase connue ne sera pas imputée.
    phase_connue = df["phase"].notna()

    print("Séquences de phase identifiées :", df["bloc_phase"].nunique())
    print("Mesures sans phase :", int((~phase_connue).sum()))
    # ------------------------------------------------------------
    # 8.4 : Définition des valeurs invalides
    # ------------------------------------------------------------

    colonnes_sans_negatif = [
        "puissance_w",
        "rayonnement",
        "tension_v",
        "courant_a",
    ]

    colonnes_valeur_libre = ["temperature_c",]

    colonnes_mesures = (colonnes_sans_negatif + colonnes_valeur_libre)

    # Conversion explicite en numérique.
    for colonne in colonnes_mesures:
        df[colonne] = pd.to_numeric(
            df[colonne],
            errors="coerce",
        )

    # Conserver les valeurs initiales pour l'audit.
    df["puissance_w_brute"] = df["puissance_w"]

    df["puissance_corrigee"] = False

    # Les valeurs négatives sont invalides pour ces mesures.
    for colonne in colonnes_sans_negatif:

        masque_negatif = df[colonne] < 0

        df.loc[masque_negatif, colonne] = np.nan

    # Une puissance supérieure à la puissance nominale
    # est traitée comme une valeur aberrante.
    puissance_nominale = pd.to_numeric(df["puissance_nominale_w"], errors="coerce",)

    masque_puissance_aberrante = (
        df["puissance_w"].notna()
        & puissance_nominale.notna()
        & (df["puissance_w"] > puissance_nominale)
    )

    df.loc[masque_puissance_aberrante, "puissance_w",] = np.nan

    df.loc[
        masque_puissance_aberrante,
        "puissance_corrigee",
    ] = True

    print("Puissances supérieures à la puissance nominale :", int(masque_puissance_aberrante.sum()),)

    # ------------------------------------------------------------
    # 8.5 : Imputation par moyenne de la même séquence
    # ------------------------------------------------------------

    cles_groupement = [df["equipement_id"], df["bloc_phase"],]

    for colonne in colonnes_mesures:

        masque_manquant = df[colonne].isna()

        # La moyenne utilise uniquement les valeurs valides
        # de cet équipement dans cette séquence de phase.
        valeurs_valides = df[colonne].where(phase_connue & df[colonne].notna())

        moyenne_sequence = valeurs_valides.groupby(cles_groupement).transform("mean")

        # Remplacement uniquement si une phase est connue
        # et si au moins une valeur valide permet une moyenne.
        masque_imputable = (
            masque_manquant
            & phase_connue
            & moyenne_sequence.notna()
        )

        nombre_a_remplacer = int(masque_imputable.sum())

        df.loc[masque_imputable, colonne,] = moyenne_sequence.loc[masque_imputable]

        df[colonne] = df[colonne].round(2)

        # Marquer également les puissances remplacées
        # à cause d'une valeur manquante ou invalide.
        if colonne == "puissance_w":
            df.loc[
                masque_imputable,
                "puissance_corrigee",
            ] = True

        print(
            f"{colonne} : "
            f"{nombre_a_remplacer} valeur(s) remplacée(s), "
            f"{int(df[colonne].isna().sum())} valeur(s) "
            "encore manquante(s)"
        )

    # Diagnostic des mesures sans phase
    sans_phase = df[df["phase"].isna()]

    print("\n--- DIAGNOSTIC DES MESURES SANS PHASE ---")
    print("Nombre de mesures :", len(sans_phase))
    print("Sites concernés :")
    print(sans_phase["site_id"].value_counts(dropna=False))

    if not sans_phase.empty:
        print("\nPremiers timestamps sans phase :")
        print(
            sans_phase[["site_id", "timestamp"]]
            .head(20)
            .to_string(index=False)
        )

    # ------------------------------------------------------------
    # 8.6 : Nettoyage des colonnes techniques
    # ------------------------------------------------------------

    df = df.drop(
        columns=[
            "puissance_nominale_w",
            "site_id",
            "bloc_phase",
        ],
        errors="ignore",
    )

    # Vérification des valeurs manquantes restantes.
    print("\nValeurs manquantes après imputation :")

    print(
        df[
            colonnes_mesures
        ].isna().sum()
    )

    print(
        "\nTotal des valeurs manquantes dans le DataFrame :",
        int(df.isna().sum().sum()),
    )

    # ------------------------------------------------------------
    # 8.7 : Export des rejets et des données nettoyées
    # ------------------------------------------------------------

    df_rejets_total = pd.concat(
        [
            df_rejets_doublons,
            df_rejets_date_manquante,
            df_rejets_date_futur,
            df_rejets_id_eq,
            df_rejets_id_site,
        ],
        ignore_index=True,
    )

    df_rejets_total.to_csv(
        dossier_sortie / REJETS_PATH,
        index=False,
    )

    df_sortie = df.copy()

    df_sortie["timestamp"] = (
        df_sortie["timestamp"]
        .dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    )

    df_sortie.to_csv(OUTPUT_PATH, index=False,)

    print(f"\nFichier nettoyé : '{OUTPUT_PATH}'")

    print("\nFichier lignes rejetées généré :", dossier_sortie / REJETS_PATH,)

    return df

def main():
    df = lire_csv()
    df = nettoyage_csv(df)

    return df
    

if __name__ == "__main__":
    main()