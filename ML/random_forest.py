
import duckdb
import numpy as np
import pandas as pd
import joblib

from pathlib import Path

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


# ============================================================
# 1. CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STAR_DB = DATA_DIR / "base_etoile.duckdb"

HORIZON_HEURES = 9
PROPORTION_TRAIN = 0.80
SEUIL_CLASSIFICATION = 0.50

RANDOM_STATE = 42


# ============================================================
# 2. CHARGEMENT DES DONNEES
# ============================================================

def charger_donnees():
    con = duckdb.connect(str(STAR_DB), read_only=True)

    try:
        query = """
            SELECT
                f.equipement_id,
                STRPTIME(
                    CAST(f.temps_id AS VARCHAR),
                    '%Y%m%d%H%M'
                ) AS timestamp,

                f.puissance_w,
                f.temperature_c,
                f.rayonnement,
                f.tension_v,
                f.courant_a,
                f.performance,
                f.perte_production,
                f.phase,
                f.rayonnement_solaire_w_m2,
                f.temperature_ambiante_c,

                e.type_equipement,
                e.puissance_nominale_w,
                e.rendement_nominal,
                e.duree_vie_annee,
                e.date_installation,
                e.site,

                da.severite AS severite_alarme

            FROM fait_production AS f

            LEFT JOIN dim_equipement AS e
                ON f.equipement_id = e.equipement_id

            LEFT JOIN dim_alarme AS da
                ON f.alarme_id = da.alarme_id

            WHERE f.temps_id IS NOT NULL
        """

        df = con.execute(query).fetchdf()

    finally:
        con.close()

    df["timestamp"] = pd.to_datetime(
        df["timestamp"], errors="coerce"
    )
    df["date_installation"] = pd.to_datetime(
        df["date_installation"], errors="coerce"
    )

    df = df.dropna(
        subset=["equipement_id", "timestamp"]
    ).copy()

    # Vérification des doublons avant déduplication.
    cles = ["equipement_id", "timestamp"]
    nb_doublons = int(df.duplicated(cles, keep=False).sum())

    print("Lignes initiales :", len(df))
    print("Lignes appartenant à un groupe de doublons :", nb_doublons)

    if nb_doublons:
        df = dedoublonner_observations(df)

    # Ancienneté calculée à la date de chaque observation.
    age_jours = (
        df["timestamp"] - df["date_installation"]
    ).dt.total_seconds() / 86400

    duree_vie = pd.to_numeric(
        df["duree_vie_annee"], errors="coerce"
    ).clip(lower=1)

    df["ratio_vie_a_date"] = (
        age_jours.clip(lower=0) / (duree_vie * 365.25)
    )

    df = df.sort_values(
        ["equipement_id", "timestamp"]
    ).reset_index(drop=True)

    print("Lignes après déduplication :", len(df))
    print("Nombre d'équipements :", df["equipement_id"].nunique())
    print("Période :", df["timestamp"].min(), "->", df["timestamp"].max())

    return df


# ============================================================
# 3. DEDOUBLONNAGE CONTROLE
# ============================================================

def dedoublonner_observations(df):
    cles = ["equipement_id", "timestamp"]
    colonne_alarme = "severite_alarme"

    colonnes_mesure = [
        c for c in df.columns
        if c not in cles + [colonne_alarme]
    ]

    groupes = df.groupby(cles, dropna=False)

    # Ne pas choisir arbitrairement entre des mesures différentes.
    conflits = (
        groupes[colonnes_mesure]
        .nunique(dropna=False)
        .gt(1)
        .any(axis=1)
    )

    if conflits.any():
        exemples = conflits[conflits].index.tolist()[:5]

        raise ValueError(
            "Mesures contradictoires pour un même équipement "
            f"et timestamp. Exemples : {exemples}. "
            "Vérifier le grain des données source."
        )

    severite = (
        df[colonne_alarme]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )

    priorite = {
        "": 0,
        "warning": 1,
        "critical": 2,
        "urgence": 3,
    }

    df = df.copy()
    df["_priorite_alarme"] = severite.map(priorite).fillna(0)

    # Une seule ligne par mesure, en conservant la sévérité
    # la plus élevée parmi les lignes dupliquées.
    df = (
        df.groupby(cles, as_index=False, dropna=False)
        .agg({
            **{c: "first" for c in colonnes_mesure},
            "_priorite_alarme": "max",
        })
    )

    libelles = {
        0: np.nan,
        1: "warning",
        2: "critical",
        3: "urgence",
    }

    df[colonne_alarme] = df.pop(
        "_priorite_alarme"
    ).map(libelles)

    return df


# ============================================================
# 4. CREATION DE LA CIBLE
# ============================================================

def creer_cible(df):
    df = df.copy()

    severite = (
        df["severite_alarme"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )

    # Cible : présence d'une alarme critique/urgente
    # STRICTEMENT après l'instant courant et dans les 9 h.
    df["evenement_cible"] = severite.isin(
        ["critical", "urgence"]
    ).astype(np.int8)

    horizon = pd.Timedelta(hours=HORIZON_HEURES)
    df["cible_degradation"] = 0

    for _, groupe in df.groupby("equipement_id", sort=False):
        indices = groupe.index.to_numpy()

        dates = groupe["timestamp"].to_numpy(
            dtype="datetime64[ns]"
        )

        evenements = groupe["evenement_cible"].to_numpy(
            dtype=np.int64
        )

        cumul = np.concatenate([
            np.array([0], dtype=np.int64),
            np.cumsum(evenements),
        ])

        debut = np.searchsorted(
            dates, dates, side="right"
        )

        fin = np.searchsorted(
            dates,
            dates + np.timedelta64(HORIZON_HEURES, "h"),
            side="right",
        )

        nombre_evenements = cumul[fin] - cumul[debut]

        df.loc[indices, "cible_degradation"] = (
            nombre_evenements > 0
        ).astype(np.int8)

    # Les observations trop proches de la fin de série
    # ne disposent pas d'un horizon complet.
    derniere_date = (
        df.groupby("equipement_id")["timestamp"]
        .transform("max")
    )

    df = df.loc[
        df["timestamp"] + horizon <= derniere_date
    ].copy()

    df = df.drop(columns=["evenement_cible"])

    print("\nRépartition de la cible :")
    print(df["cible_degradation"].value_counts().sort_index())

    print(
        "Proportion positive :",
        round(df["cible_degradation"].mean(), 4),
    )

    return df


# ============================================================
# 5. PREPARATION DES VARIABLES
# ============================================================

def preparer_variables(df):
    numeriques = [
        "puissance_w",
        "temperature_c",
        "rayonnement",
        "tension_v",
        "courant_a",
        "performance",
        "perte_production",
        "rayonnement_solaire_w_m2",
        "temperature_ambiante_c",
        "puissance_nominale_w",
        "rendement_nominal",
        "duree_vie_annee",
        "ratio_vie_a_date",
    ]

    categorielles = [
        "type_equipement",
        "phase",
        "site",
    ]

    X = df[numeriques + categorielles].copy()
    y = df["cible_degradation"].astype(int)

    for colonne in numeriques:
        X[colonne] = pd.to_numeric(
            X[colonne], errors="coerce"
        )

    for colonne in categorielles:
        X[colonne] = X[colonne].astype("string")

    return X, y, numeriques, categorielles


# ============================================================
# 6. ENTRAINEMENT ET TEST TEMPOREL
# ============================================================

def entrainer_modele(df):
    X, y, numeriques, categorielles = preparer_variables(df)

    dates_uniques = np.sort(df["timestamp"].unique())

    if len(dates_uniques) < 2:
        raise ValueError(
            "Pas assez de dates distinctes pour séparer "
            "les données en entraînement et en test."
        )

    position = int(len(dates_uniques) * PROPORTION_TRAIN)
    position = min(
        max(position, 1),
        len(dates_uniques) - 1,
    )

    date_separation = pd.Timestamp(dates_uniques[position])

    # Purge des observations d'entraînement dont l'horizon
    # de cible chevaucherait la période de test.
    date_fin_train = (
        date_separation - pd.Timedelta(hours=HORIZON_HEURES)
    )

    masque_train = df["timestamp"] < date_fin_train
    masque_test = df["timestamp"] >= date_separation

    X_train = X.loc[masque_train]
    X_test = X.loc[masque_test]

    y_train = y.loc[masque_train]
    y_test = y.loc[masque_test]

    if y_train.nunique() < 2:
        raise ValueError(
            "L'entraînement doit contenir les deux classes. "
            "Vérifier la cible et la période choisie."
        )

    if y_test.empty:
        raise ValueError("Le jeu de test est vide.")

    print("\nDate de séparation :", date_separation)
    print("Taille entraînement :", len(X_train))
    print("Taille test :", len(X_test))
    print("Taux positif entraînement :", round(y_train.mean(), 4))
    print("Taux positif test :", round(y_test.mean(), 4))

    traitement_numerique = Pipeline([
        ("imputation", SimpleImputer(strategy="median")),
    ])

    traitement_categoriel = Pipeline([
        ("imputation", SimpleImputer(strategy="most_frequent")),
        ("encodage", OneHotEncoder(handle_unknown="ignore")),
    ])

    preparation = ColumnTransformer([
        ("numerique", traitement_numerique, numeriques),
        ("categoriel", traitement_categoriel, categorielles),
    ])

    modele = Pipeline([
        ("preparation", preparation),
        ("classification", RandomForestClassifier(
            n_estimators=400,
            max_depth=None,
            min_samples_split=5,
            min_samples_leaf=2,
            class_weight=None,
            random_state=RANDOM_STATE,
            n_jobs=-1,
        )),
    ])

    modele.fit(X_train, y_train)

    # Probabilité estimée par la forêt pour la classe positive.
    position_positive = list(
        modele.named_steps["classification"].classes_
    ).index(1)

    scores = modele.predict_proba(X_test)[:, position_positive]

    seuil = SEUIL_CLASSIFICATION
    predictions = (scores >= seuil).astype(int)

    print("\n===== RESULTATS RANDOM FOREST =====")
    print("Seuil :", seuil)

    print(
        "Balanced accuracy :",
        round(balanced_accuracy_score(y_test, predictions), 4),
    )

    print(
        "Précision :",
        round(
            precision_score(y_test, predictions, zero_division=0),
            4,
        ),
    )

    print(
        "Rappel :",
        round(
            recall_score(y_test, predictions, zero_division=0),
            4,
        ),
    )

    print(
        "F1-score :",
        round(f1_score(y_test, predictions, zero_division=0), 4),
    )

    print("\nRapport de classification :")
    print(classification_report(
        y_test,
        predictions,
        labels=[0, 1],
        target_names=["Pas d'alarme cible", "Alarme cible"],
        zero_division=0,
    ))

    print("Matrice de confusion (réel en lignes) :")
    print(confusion_matrix(
        y_test, predictions, labels=[0, 1]
    ))

    if y_test.nunique() == 2:
        print(
            "ROC-AUC :",
            round(roc_auc_score(y_test, scores), 4),
        )

        if y_test.sum() > 0:
            print(
                "Average Precision :",
                round(average_precision_score(y_test, scores), 4),
            )

    # Export des prédictions du test.
    resultats = df.loc[
        masque_test,
        ["equipement_id", "timestamp", "cible_degradation"],
    ].copy()

    resultats["score_random_forest"] = scores
    resultats["prediction"] = predictions

    chemin_resultats = DATA_DIR / "predictions_random_forest.csv"
    resultats.to_csv(chemin_resultats, index=False)

    print("\nPrédictions enregistrées dans :", chemin_resultats)

    # Importance des variables après encodage.
    noms_variables = modele.named_steps[
        "preparation"
    ].get_feature_names_out()

    importances = (
        modele.named_steps["classification"].feature_importances_
    )

    df_importances = pd.DataFrame({
        "variable": noms_variables,
        "importance": importances,
    }).sort_values("importance", ascending=False)

    chemin_importances = DATA_DIR / "importances_random_forest.csv"
    df_importances.to_csv(chemin_importances, index=False)

    print("\nTop 15 des variables importantes :")
    print(df_importances.head(15).to_string(index=False))

    print("\nImportances enregistrées dans :", chemin_importances)

    chemin_modele = DATA_DIR / "modele_random_forest.joblib"
    joblib.dump(modele, chemin_modele)

    print("Modèle sauvegardé dans :", chemin_modele)

    return modele


# ============================================================
# 7. EXECUTION
# ============================================================

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    df = charger_donnees()
    df = creer_cible(df)
    entrainer_modele(df)


if __name__ == "__main__":
    main()
