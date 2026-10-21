import duckdb
import numpy as np
import pandas as pd

from pathlib import Path

from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import (mean_absolute_error, mean_squared_error, confusion_matrix, precision_score, recall_score,)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    precision_score,
    recall_score,
    roc_auc_score,
)


# ============================================================
# 1. CHEMINS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STAR_DB = DATA_DIR / "base_etoile.duckdb"

HORIZON_HEURES = 9


# ============================================================
# 2. CHARGEMENT DES DONNEES DEPUIS DUCKDB
# ============================================================
def dedoublonner_observations(df):
    cles = ["equipement_id", "timestamp"]
    colonne_alarme = "severite_alarme"

    colonnes_mesure = [
        c for c in df.columns
        if c not in cles + [colonne_alarme]
    ]

    doublons = df.duplicated(subset=cles, keep=False)

    if not doublons.any():
        return df

    groupes = df.loc[doublons].groupby(cles, dropna=False)

    # Les données de production doivent être cohérentes
    # entre les lignes dupliquées.
    conflits = (
        groupes[colonnes_mesure]
        .nunique(dropna=False)
        .gt(1)
        .any(axis=1)
    )

    if conflits.any():
        exemples = conflits[conflits].index.tolist()[:5]
        raise ValueError(
            "Mesures différentes pour un même équipement "
            f"et timestamp. Exemples de clés : {exemples}"
        )

    # Priorité explicite : urgence > critical > warning.
    severite = (
        df[colonne_alarme]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )

    rangs = {
        "": 0,
        "warning": 1,
        "critical": 2,
        "urgence": 3,
    }

    df = df.copy()
    df["_rang_alarme"] = severite.map(rangs).fillna(0)

    aggregations = {
        c: "first" for c in colonnes_mesure
    }
    aggregations["_rang_alarme"] = "max"

    df = (
        df.groupby(cles, as_index=False, dropna=False)
        .agg(aggregations)
    )

    libelles = {
        0: np.nan,
        1: "warning",
        2: "critical",
        3: "urgence",
    }

    df[colonne_alarme] = (
        df.pop("_rang_alarme")
        .map(libelles)
    )

    return df

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

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["date_installation"] = pd.to_datetime(df["date_installation"], errors="coerce")

    df = df.dropna(subset=["equipement_id", "timestamp"])

    nb_avant = len(df)
    df = dedoublonner_observations(df)
    print("Doublons supprimés :", nb_avant - len(df))

    df = df.sort_values(["equipement_id", "timestamp"]).reset_index(drop=True)

    # Ancienneté calculée à la date de chaque observation.
    age_jours = (df["timestamp"] - df["date_installation"]).dt.total_seconds() / 86400

    df["ratio_vie_a_date"] = (age_jours.clip(lower=0) / (df["duree_vie_annee"].clip(lower=1) * 365.25))

    print("Nombre de lignes :", len(df))
    print("Nombre d'équipements :", df["equipement_id"].nunique())
    print("Période :", df["timestamp"].min(), "->", df["timestamp"].max())

    return df


# ============================================================
# 3. CREATION DE LA CIBLE
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

    # Définition provisoire de l'événement de dégradation.
    df["evenement_cible"] = severite.isin(["critical", "urgence"]).astype(int)

    horizon = pd.Timedelta(hours=HORIZON_HEURES)

    # Calcul des cibles sur l'historique complet.
    df["cible_degradation"] = 0

    for _, groupe in df.groupby("equipement_id", sort=False):
        indices = groupe.index.to_numpy()
        dates = groupe["timestamp"].to_numpy(dtype="datetime64[ns]")
        evenements = groupe["evenement_cible"].to_numpy(dtype=np.int64)

        cumul = np.concatenate([np.array([0], dtype=np.int64), np.cumsum(evenements)])

        debut = np.searchsorted(dates, dates, side="right")

        fin = np.searchsorted(dates, dates + np.timedelta64(HORIZON_HEURES, "h"), side="right")

        nombre_evenements = cumul[fin] - cumul[debut]

        df.loc[indices, "cible_degradation"] = (nombre_evenements > 0).astype(int)

    # On retire seulement ensuite les lignes pour lesquelles
    # la fenêtre future complète de 9 heures n'est pas observable.
    derniere_date = df.groupby("equipement_id")["timestamp"].transform("max")

    df = df.loc[df["timestamp"] + horizon <= derniere_date].copy()

    df = df.drop(columns=["evenement_cible"])

    print("\nRépartition de la cible :")
    print(df["cible_degradation"].value_counts())
    print("Proportion positive :", round(df["cible_degradation"].mean(), 4))

    return df


# ============================================================
# 4. PREPARATION DES VARIABLES
# ============================================================

def preparer_variables(df):

    variables_numeriques = [
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

    variables_categorielles = [
        "type_equipement",
        "phase",
        "site",
    ]

    X = df[variables_numeriques + variables_categorielles].copy()

    y = df["cible_degradation"].astype(int)

    # Conversion explicite des mesures en valeurs numériques.
    for colonne in variables_numeriques:
        X[colonne] = pd.to_numeric(X[colonne], errors="coerce")

    for colonne in variables_categorielles:
        X[colonne] = X[colonne].astype("string")

    return (X, y, variables_numeriques, variables_categorielles,)


# ============================================================
# 5. ENTRAINEMENT ET EVALUATION TEMPORELLE
# ============================================================

def entrainer_modele(df):

    X, y, numeriques, categorielles = preparer_variables(df)

    # Séparation chronologique : on entraîne sur le passé
    # et on teste sur la période la plus récente.
    dates_uniques = np.sort(df["timestamp"].unique())

    if len(dates_uniques) < 2:
        raise ValueError(
            "Pas assez de dates distinctes pour séparer "
            "les données en entraînement et en test."
        )

    position = int(len(dates_uniques) * 0.80)
    position = min(max(position, 1), len(dates_uniques) - 1)

    date_separation = dates_uniques[position]

    # On laisse un intervalle de 9 heures entre la fin
    # des dates d'entraînement et le début du test.
    date_fin_train = (pd.Timestamp(date_separation) - pd.Timedelta(hours=HORIZON_HEURES))

    masque_train = df["timestamp"] < date_fin_train
    masque_test = df["timestamp"] >= date_separation

    X_train, X_test = X.loc[masque_train], X.loc[masque_test]
    y_train, y_test = y.loc[masque_train], y.loc[masque_test]

    if y_train.nunique() < 2:
        raise ValueError(
            "La cible d'entraînement ne contient qu'une seule "
            "classe. Vérifie la définition de la dégradation "
            "et la répartition temporelle des événements."
        )

    if y_test.empty:
        raise ValueError("Le jeu de test est vide.")

    print("\nDate de séparation :", date_separation)
    print("Taille entraînement :", len(X_train))
    print("Taille test :", len(X_test))
    print("Taux de dégradation entraînement :", round(y_train.mean(), 4))
    print("Taux de dégradation test :", round(y_test.mean(), 4))

    traitement_numerique = Pipeline([
        ("imputation", SimpleImputer(strategy="median")),
        ("normalisation", StandardScaler()),
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
        ("classification", LogisticRegression(
            max_iter=2000,
            class_weight=None,
            random_state=42,
        )),
    ])

    modele.fit(X_train, y_train)

    scores = modele.predict_proba(X_test)[:, 1]

    # Seuil initial, à ajuster sur un jeu de validation.
    seuil = 0.5
    predictions = (scores >= seuil).astype(int)

    print("\n===== RESULTATS SUR LE TEST =====")
    print("Seuil :", seuil)
    print("MAE :", round(mean_absolute_error(y_test, scores), 4))
    print("RMSE :", round(np.sqrt(mean_squared_error(y_test, scores)), 4))
    print("Précision :", round(precision_score(y_test, predictions, zero_division=0), 4))
    print("Rappel :", round(recall_score(y_test, predictions, zero_division=0), 4))
    print("Balanced accuracy :", round(balanced_accuracy_score(y_test, predictions), 4))
    print("Matrice de confusion (lignes = réel, colonnes = prédit) :")
    print(confusion_matrix(y_test, predictions, labels=[0, 1]))

    if y_test.nunique() == 2:
        print("ROC-AUC :", round(roc_auc_score(y_test, scores), 4))

    if y_test.sum() > 0:
        print("Average Precision :", round(average_precision_score(y_test, scores), 4))

    # Sauvegarde des prédictions pour analyse.
    resultats = df.loc[masque_test, ["equipement_id", "timestamp", "cible_degradation"]].copy()

    resultats["score_lineaire"] = scores
    resultats["prediction"] = predictions

    chemin_resultats = DATA_DIR / "predictions_test.csv"
    resultats.to_csv(chemin_resultats, index=False)

    print("\nRésultats enregistrés dans :", chemin_resultats)

    return modele


# ============================================================
# 6. EXECUTION
# ============================================================

def main():
    df = charger_donnees()
    df = creer_cible(df)
    modele = entrainer_modele(df)


if __name__ == "__main__":
    main()
