"""Maintenance prédictive : risque de dégradation d'un équipement dans les 9 h.

Usage : python maintenance_predictive.py [chemin/base_etoile.duckdb]

Définition de la cible y(t) = 1 si, dans les 9 h suivantes (t, t+9h] :
  - la performance moyenne HORS ÉCLIPSE passe sous 0,65
  OU
  - au moins une alarme 'critical' (ou 'URGENCE') de type perte_puissance / surchauffe
    est enregistrée.
"""
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, confusion_matrix, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# ----------------------------- Paramètres ---------------------------------
STAR_DB = (Path(sys.argv[1]) if len(sys.argv) > 1
           else Path(__file__).resolve().parent / "data" / "base_etoile.duckdb")

PAS_MIN = 5                      # pas de la télémétrie (minutes)
HORIZON_H = 9                    # horizon de prédiction (6 périodes orbitales de 90 min)
H = HORIZON_H * 60 // PAS_MIN    # 108 pas
SEUIL_PERF = 0.65
PERF_MAX = 1.5                                      # au-delà : mesure invalide
PERF_TYPES = {"panneau_solaire", "convertisseur_DC"}  # types où la performance a un sens
ECLIPSE_REGEX = "eclipse|éclipse"            # à adapter aux valeurs de la colonne phase
ALARMES_CIBLES = {"perte_puissance", "surchauffe"}
SEVERITES_CRITIQUES = {"critical", "urgence"}  # comparées en minuscules

SEULEMENT_SAINS = True   # True : prédire l'entrée en dégradation (équipements sains à t)

COUT_FN, COUT_FP, COUT_TP, COUT_TN = 2500, 150, 300, 0
PART_TRAIN, PART_VAL = 0.60, 0.80            # coupures temporelles (60 % / 20 % / 20 %)


# ----------------------------- Données ------------------------------------
def charger():
    con = duckdb.connect(str(STAR_DB), read_only=True)
    df = con.execute("""
        SELECT strptime(CAST(f.temps_id AS VARCHAR), '%Y%m%d%H%M') AS ts,
               f.equipement_id, e.type_equipement, e.puissance_nominale_w,
               f.puissance_w, f.temperature_c, f.tension_v, f.courant_a,
               f.rayonnement, f.performance, f.ratio_vie_equipement, f.phase,
               f.rayonnement_solaire_w_m2, f.temperature_ambiante_c,
               (f.maintenance_id IS NOT NULL) AS en_maintenance,
               a.type_alarme, a.severite
        FROM fait_production f
        JOIN dim_equipement e ON e.equipement_id = f.equipement_id
        LEFT JOIN dim_alarme a ON a.alarme_id = f.alarme_id
    """).fetchdf()
    con.close()
    return df


def futur(s, fonction, min_periods):
    """Agrégat sur la fenêtre (t, t+H] : exclut t, regarde uniquement vers l'avant."""
    inverse = s[::-1].rolling(H, min_periods=min_periods)
    return getattr(inverse, fonction)()[::-1].shift(-1)


def construire(df):
    for c in ["puissance_nominale_w", "puissance_w", "temperature_c", "tension_v",
              "courant_a", "rayonnement", "performance", "ratio_vie_equipement",
              "rayonnement_solaire_w_m2", "temperature_ambiante_c"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)

    # Qualité de la performance : (1) valeurs physiquement impossibles -> invalides ;
    # (2) types d'équipement pour lesquels puissance/puissance nominale n'est pas un
    # indicateur de santé (ex. batterie) -> critère de performance non applicable.
    n_aberrantes = int((df["performance"] > PERF_MAX).sum())
    df.loc[df["performance"] > PERF_MAX, "performance"] = np.nan
    df.loc[~df["type_equipement"].isin(PERF_TYPES), "performance"] = np.nan
    print(f"Performance : {n_aberrantes} valeur(s) > {PERF_MAX} mises à NaN ; "
          f"critère appliqué seulement à {sorted(PERF_TYPES)}")

    df = df.sort_values(["equipement_id", "ts"]).reset_index(drop=True)
    g = df.groupby("equipement_id", sort=False)

    eclipse = df["phase"].fillna("").str.contains(ECLIPSE_REGEX, case=False, regex=True)
    if not eclipse.any():
        raise ValueError(f"Aucune éclipse détectée. Valeurs de phase : "
                         f"{df['phase'].value_counts(dropna=False).to_dict()} "
                         f"-> adapte ECLIPSE_REGEX.")
    df["is_eclipse"] = eclipse.astype(int)
    df["perf_hors_eclipse"] = df["performance"].where(~eclipse)
    df["alarme_cible"] = (df["type_alarme"].isin(ALARMES_CIBLES)
                          & df["severite"].str.lower().isin(SEVERITES_CRITIQUES)).astype(int)
    df["en_maintenance"] = df["en_maintenance"].astype(int)

    # ---- Variables explicatives : uniquement le PASSÉ (fenêtres vers l'arrière) ----
    feats_roulantes = []
    for col in ["puissance_w", "temperature_c", "performance"]:
        gc = g[col]
        df[f"{col}_moy_1h"] = gc.transform(lambda s: s.rolling(12, min_periods=6).mean())
        df[f"{col}_std_1h"] = gc.transform(lambda s: s.rolling(12, min_periods=6).std())
        df[f"{col}_moy_3h"] = gc.transform(lambda s: s.rolling(36, min_periods=12).mean())
        df[f"{col}_delta_1h"] = gc.diff(12)
        feats_roulantes += [f"{col}_moy_1h", f"{col}_std_1h", f"{col}_moy_3h", f"{col}_delta_1h"]
    df["perf_he_3h"] = g["perf_hors_eclipse"].transform(
        lambda s: s.rolling(36, min_periods=6).mean())
    df["alarmes_3h"] = g["alarme_cible"].transform(
        lambda s: s.rolling(36, min_periods=1).sum())
    heure = df["ts"].dt.hour + df["ts"].dt.minute / 60
    df["h_sin"] = np.sin(2 * np.pi * heure / 24)
    df["h_cos"] = np.cos(2 * np.pi * heure / 24)

    # ---- Cible : fenêtre vers l'AVENIR (t, t+9h] ----
    df["fut_perf"] = g["perf_hors_eclipse"].transform(lambda s: futur(s, "mean", 12))
    df["fut_alarme"] = g["alarme_cible"].transform(lambda s: futur(s, "sum", 1))
    df["reste"] = g.cumcount(ascending=False)
    df["y"] = ((df["fut_perf"] < SEUIL_PERF) | (df["fut_alarme"] > 0)).astype(int)

    # ---- Dates de référence : une par heure, horizon complet seulement ----
    # Si SEULEMENT_SAINS : on ne prédit que l'ENTRÉE en dégradation, donc on écarte les
    # équipements déjà dégradés à l'instant t (performance hors éclipse des 3 dernières
    # heures sous le seuil, ou alarme critique récente).
    sain = (df["perf_he_3h"].isna() | (df["perf_he_3h"] >= SEUIL_PERF)) & (df["alarmes_3h"] == 0)
    masque = (df["ts"].dt.minute == 0) & (df["reste"] >= H)
    if SEULEMENT_SAINS:
        masque &= sain
    ref = df[masque].copy()
    print(f"Dates de référence retenues : {len(ref)} (sur {int(((df['ts'].dt.minute == 0) & (df['reste'] >= H)).sum())})")

    print("\nDiagnostic : % de positifs par type d'équipement")
    print(ref.groupby("type_equipement")["y"].agg(["mean", "count"]).round(3))
    print("\nDiagnostic : % de positifs par semaine")
    print(ref.groupby(ref["ts"].dt.to_period("W"))["y"].mean().round(3))
    print("\nDiagnostic : performance hors éclipse par type")
    print(df.groupby("type_equipement")["perf_hors_eclipse"]
          .describe(percentiles=[.1, .5, .9]).round(2))

    num = (["puissance_w", "temperature_c", "tension_v", "courant_a", "rayonnement",
            "rayonnement_solaire_w_m2", "temperature_ambiante_c", "performance",
            "ratio_vie_equipement", "puissance_nominale_w", "is_eclipse",
            "en_maintenance", "alarmes_3h", "perf_he_3h", "h_sin", "h_cos"]
           + feats_roulantes)
    X = pd.concat([ref[num],
                   pd.get_dummies(ref["type_equipement"], prefix="type", dtype=int)], axis=1)
    return ref, X, ref["y"].to_numpy()


def decouper(ref):
    """Découpage chronologique avec embargo de 9 h (les cibles regardent 9 h devant)."""
    t0, t1 = ref["ts"].min(), ref["ts"].max()
    span, gap = t1 - t0, pd.Timedelta(hours=HORIZON_H)
    fin_train = t0 + PART_TRAIN * span
    fin_val = t0 + PART_VAL * span
    ts = ref["ts"]
    return (ts <= fin_train).to_numpy(), \
           ((ts >= fin_train + gap) & (ts <= fin_val)).to_numpy(), \
           (ts >= fin_val + gap).to_numpy()


# ----------------------------- Coût métier --------------------------------
def cout(y, pred):
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return fn * COUT_FN + fp * COUT_FP + tp * COUT_TP + tn * COUT_TN


def chercher_seuil(y_val, p_val):
    seuils = np.linspace(0.01, 0.99, 99)
    couts = np.array([cout(y_val, p_val >= s) for s in seuils])
    i = int(np.argmin(couts))
    return float(seuils[i]), float(couts[i])


def afficher_cm(y, pred, titre):
    cm = confusion_matrix(y, pred, labels=[0, 1])
    print(f"\n{titre}")
    print(pd.DataFrame(cm, index=["réel 0 (sain)", "réel 1 (risque)"],
                       columns=["prédit 0", "prédit 1"]))


# ----------------------------- Programme ----------------------------------
def main():
    ref, X, y = construire(charger())
    tr, va, te = decouper(ref)
    for nom, m in [("train", tr), ("validation", va), ("test", te)]:
        print(f"{nom:<11}: {m.sum():>6} lignes | {ref.loc[m, 'ts'].min()} -> "
              f"{ref.loc[m, 'ts'].max()} | % positifs = {100 * y[m].mean():.1f}")
    if min(y[tr].sum(), y[va].sum(), y[te].sum()) == 0:
        raise ValueError("Un des jeux n'a aucun cas positif : revoir la définition de la cible.")

    modeles = {
        "Régression logistique": Pipeline([
            ("imp", SimpleImputer(strategy="median")),
            ("sc", StandardScaler()),
            ("clf", LogisticRegression(max_iter=2000))]),
        # early_stopping=False : sinon sklearn tire un jeu de validation AU HASARD
        "Gradient Boosting": HistGradientBoostingClassifier(
            max_depth=4, learning_rate=0.05, max_iter=200, early_stopping=False,
            random_state=0),
    }

    resultats = {}
    for nom, modele in modeles.items():
        modele.fit(X[tr], y[tr])
        p_val = modele.predict_proba(X[va])[:, 1]
        seuil, c_val = chercher_seuil(y[va], p_val)          # seuil choisi sur validation
        resultats[nom] = (seuil, c_val)
        print(f"\n=== {nom} ===")
        print(f"AUC validation = {roc_auc_score(y[va], p_val):.3f} | "
              f"AP validation = {average_precision_score(y[va], p_val):.3f}")
        print(f"Seuil optimal (validation) = {seuil:.2f} -> coût validation = {c_val:,.0f} €")

        # ---- Test : UNE seule application du seuil figé ----
        p_te = modele.predict_proba(X[te])[:, 1]
        pred_te = p_te >= seuil
        afficher_cm(y[te], pred_te, f"Matrice de confusion TEST (seuil {seuil:.2f})")
        print(f"Coût total test = {cout(y[te], pred_te):,.0f} €")

    pos, neg = int(y[te].sum()), int((1 - y[te]).sum())
    print(f"\nRéférences sur le test : jamais d'inspection = {pos * COUT_FN:,.0f} € | "
          f"toujours inspecter = {pos * COUT_TP + neg * COUT_FP:,.0f} €")

    # ---- Importance des variables ----
    lr = modeles["Régression logistique"]
    coef = pd.Series(lr.named_steps["clf"].coef_[0], index=X.columns)
    print("\nRégression logistique : coefficients standardisés (top 10)")
    print(coef.reindex(coef.abs().sort_values(ascending=False).index).head(10).round(3))

    imp = permutation_importance(modeles["Gradient Boosting"], X[va], y[va],
                                 scoring="average_precision", n_repeats=5,
                                 random_state=0, n_jobs=-1)
    print("\nGradient Boosting : importance par permutation sur validation (top 10)")
    print(pd.Series(imp.importances_mean, index=X.columns)
          .sort_values(ascending=False).head(10).round(4))


if __name__ == "__main__":
    main()