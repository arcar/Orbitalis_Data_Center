import sys
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import GroupKFold
from sklearn.metrics import (average_precision_score, precision_recall_curve,
                             classification_report, confusion_matrix,
                             ConfusionMatrixDisplay, balanced_accuracy_score,
                             roc_auc_score, precision_score, recall_score)

# ============================== CONFIG ==============================
STAR_DB = (Path(sys.argv[1]) if len(sys.argv) > 1
           else Path(__file__).resolve().parent / "data" / "base_etoile.duckdb")

PAS_MIN = 5                      # pas de la télémétrie (minutes)
HORIZON_H = 9                    # horizon de prédiction (6 périodes orbitales de 90 min)
H = HORIZON_H * 60 // PAS_MIN    # 108 pas
PERIODE = 90 // PAS_MIN          # 18 pas par orbite
SEUIL_PERF = 0.45                # seuil sur la performance AU SOLEIL
PHASE_SOLEIL = None              # None = détection auto ; sinon "ensoleillement"
PRECISION_CIBLE = 0.90           # pour la comparaison à précision fixée
# ====================================================================

# ---------- 1. Chargement ----------
con = duckdb.connect(str(STAR_DB), read_only=True)
df = con.execute("""
    SELECT
        f.equipement_id,
        make_timestamp(t.annee, t.mois, t.jour, t.heure, t.minute, 0) AS ts,
        f.puissance_w::DOUBLE              AS puissance_w,
        f.temperature_c::DOUBLE            AS temperature_c,
        f.rayonnement::DOUBLE              AS rayonnement,
        f.tension_v::DOUBLE                AS tension_v,
        f.courant_a::DOUBLE                AS courant_a,
        f.performance::DOUBLE              AS performance,
        f.ratio_vie_equipement::DOUBLE     AS ratio_vie,
        f.rayonnement_solaire_w_m2::DOUBLE AS ray_solaire,
        f.temperature_ambiante_c::DOUBLE   AS temp_amb,
        f.phase,
        e.type_equipement,
        e.puissance_nominale_w::DOUBLE     AS p_nominale,
        e.rendement_nominal::DOUBLE        AS rend_nominal,
        a.severite,
        m.type_maintenance
    FROM fait_production f
    JOIN dim_temps t       ON t.temps_id = f.temps_id
    JOIN dim_equipement e  ON e.equipement_id = f.equipement_id
    LEFT JOIN dim_alarme a      ON a.alarme_id = f.alarme_id
    LEFT JOIN dim_maintenance m ON m.maintenance_id = f.maintenance_id
    ORDER BY f.equipement_id, ts
""").df()
con.close()

print("Phases et performance moyenne :")
print(df.groupby("phase")["performance"].agg(["count", "mean", "median"]))

if PHASE_SOLEIL is None:
    PHASE_SOLEIL = df.groupby("phase")["performance"].mean().idxmax()
print(f"\nPhase ensoleillée retenue : {PHASE_SOLEIL!r}")

soleil = df[df["phase"] == PHASE_SOLEIL]["performance"]
print("Performance au soleil :")
print(soleil.describe())
print(f"Part au soleil sous {SEUIL_PERF} : {(soleil < SEUIL_PERF).mean():.2%}\n")

# ---------- 2. Cible + features, équipement par équipement ----------
SIGNAUX_PHYSIQUES = ["temperature_c", "tension_v", "courant_a",
                     "puissance_w", "ray_solaire", "ecart_temp"]

def construire(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("ts").drop_duplicates("ts").set_index("ts").copy()
    au_soleil = (g["phase"] == PHASE_SOLEIL)
    p = g["performance"]
    p_soleil = p.where(au_soleil)                        # NaN pendant l'éclipse

    # Cible : la perf au soleil passe sous le seuil au moins une fois dans les H prochains pas
    futur_min = p_soleil[::-1].rolling(H, min_periods=1).min()[::-1].shift(-1)
    g["y"] = (futur_min < SEUIL_PERF).astype(float)
    g.loc[futur_min.isna(), "y"] = np.nan

    # Délai avant la prochaine chute (heures) : analyse uniquement, pas une variable du modèle
    t_evt = pd.Series(g.index, index=g.index).where((p_soleil < SEUIL_PERF).values)
    t_next = t_evt.shift(-1).bfill()
    g["delai_h"] = (t_next - pd.Series(g.index, index=g.index)).dt.total_seconds() / 3600

    # --- Historique de la performance au soleil
    ps = p_soleil.ffill(limit=PERIODE)                   # dernière valeur solaire connue
    for lag in [1, 2, 3, PERIODE // 2, PERIODE, 2 * PERIODE, 6 * PERIODE]:
        g[f"perf_lag_{lag}"] = ps.shift(lag)
    for w in [PERIODE, 3 * PERIODE, 6 * PERIODE]:
        g[f"perf_mean_{w}"] = p_soleil.rolling(w, min_periods=3).mean()
        g[f"perf_std_{w}"] = p_soleil.rolling(w, min_periods=3).std()
        g[f"perf_min_{w}"] = p_soleil.rolling(w, min_periods=3).min()
    g["perf_tendance"] = p_soleil.rolling(6 * PERIODE, min_periods=10).mean().diff(PERIODE)
    g["dist_seuil"] = ps - SEUIL_PERF

    # État courant : dernière performance connue au soleil encore au-dessus du seuil
    g["etat_ok"] = (ps >= SEUIL_PERF).astype(float)

    # --- Signaux physiques instantanés (variables de base)
    g["ecart_temp"] = g["temperature_c"] - g["temp_amb"]
    for c in ["temperature_c", "tension_v", "courant_a", "puissance_w"]:
        g[f"{c}_mean_{PERIODE}"] = g[c].rolling(PERIODE).mean()
        g[f"{c}_diff"] = g[c].diff()

    # --- Alarmes et maintenance (historique uniquement)
    alarme = g["severite"].notna().astype(float)
    g["nb_alarmes_1orb"] = alarme.rolling(PERIODE).sum()
    g["nb_alarmes_6orb"] = alarme.rolling(6 * PERIODE).sum()
    g["en_maintenance"] = g["type_maintenance"].notna().astype(float)
    g["maint_6orb"] = g["en_maintenance"].rolling(6 * PERIODE).sum()

    # --- NOUVEAU : tendances physiques au soleil (préfixe phy_), sur 3 et 6 orbites
    for c in SIGNAUX_PHYSIQUES:
        s = g[c].where(au_soleil)
        for w in [3 * PERIODE, 6 * PERIODE]:
            m = s.rolling(w, min_periods=10).mean()
            g[f"phy_{c}_m{w}"] = m
            g[f"phy_{c}_std{w}"] = s.rolling(w, min_periods=10).std()
            g[f"phy_{c}_pente{w}"] = m.diff(w)           # variation d'une fenêtre à la précédente
    g["phy_perf_pente_54"] = p_soleil.rolling(3 * PERIODE, min_periods=10).mean().diff(3 * PERIODE)
    g["phy_perf_pente_108"] = p_soleil.rolling(6 * PERIODE, min_periods=10).mean().diff(6 * PERIODE)
    return g

data = pd.concat([construire(g) for _, g in df.groupby("equipement_id")])
data = pd.get_dummies(data, columns=["phase", "type_equipement", "severite"],
                      dtype=float, drop_first=True)
data = data.drop(columns=["type_maintenance"])
data = data.dropna(subset=["y"])

# On ne garde que les moments où l'équipement va encore bien
n_avant = len(data)
data = data[data["etat_ok"] == 1].drop(columns="etat_ok")
print(f"Lignes conservées (état courant OK) : {len(data)} / {n_avant}")
data = data.sort_index()

print("\nTaux de chutes par mois :")
print(data.groupby(data.index.to_period("M"))["y"].agg(["mean", "count"]).round(3).to_string())

# ---------- 3. Split temporel avec trou de H pas ----------
coupure = data.index[int(len(data) * 0.8)]
fin_train = coupure - pd.Timedelta(minutes=PAS_MIN * H)
train, test = data[data.index < fin_train], data[data.index >= coupure]

exclues = ("y", "equipement_id", "delai_h")
feats_all = [c for c in data.columns if c not in exclues]
feats_base = [c for c in feats_all if not c.startswith("phy_")]
print(f"\nVariables : base = {len(feats_base)}, avec tendances physiques = {len(feats_all)}")

mediane = train[feats_all].median()
Xtr = train[feats_all].fillna(mediane)
Xte = test[feats_all].fillna(mediane)
y_train, y_test = train["y"].astype(int), test["y"].astype(int)

print(f"Lignes train : {len(train)}   test : {len(test)}")
print(f"Taux positif  train : {y_train.mean():.3%}   test : {y_test.mean():.3%}")
if y_train.nunique() < 2 or y_test.nunique() < 2:
    raise SystemExit("Une seule classe dans train ou test : changez SEUIL_PERF ou le découpage.")

# ---------- 4. Modèles ----------
def rf_model():
    return RandomForestClassifier(
        n_estimators=200, max_depth=12, min_samples_leaf=20, max_samples=0.3,
        class_weight="balanced_subsample", n_jobs=-1, random_state=42)

def logit_model():
    return make_pipeline(StandardScaler(),
                         LogisticRegression(max_iter=3000, class_weight="balanced", C=1.0))

modeles = {
    "Logistique (base)":     (logit_model(), feats_base),
    "Logistique (+phys)":    (logit_model(), feats_all),
    "Random Forest (base)":  (rf_model(),    feats_base),
    "Random Forest (+phys)": (rf_model(),    feats_all),
}
probas = {}
for nom, (mdl, fs) in modeles.items():
    print(f"Entraînement : {nom} ...")
    mdl.fit(Xtr[fs], y_train)
    probas[nom] = mdl.predict_proba(Xte[fs])[:, 1]

# Référence de persistance
base_pred = (test["perf_min_18"].fillna(1) < SEUIL_PERF * 1.15).astype(int)

def seuil_f1(proba):
    prec, rec, thr = precision_recall_curve(y_test, proba)
    f1 = 2 * prec * rec / (prec + rec + 1e-9)
    return thr[np.argmax(f1[:-1])]

def seuil_precision(proba, cible):
    prec, rec, thr = precision_recall_curve(y_test, proba)
    idx = np.where(prec[:-1] >= cible)[0]
    return thr[idx[0]] if len(idx) else thr[-1]

preds_f1 = {nom: (p >= seuil_f1(p)).astype(int) for nom, p in probas.items()}
preds_prec = {nom: (p >= seuil_precision(p, PRECISION_CIBLE)).astype(int)
              for nom, p in probas.items()}

# ---------- 5. Tableau comparatif ----------
def ligne(nom, proba, pred):
    return {"modèle": nom,
            "ROC-AUC": roc_auc_score(y_test, proba),
            "PR-AUC": average_precision_score(y_test, proba),
            "balanced acc": balanced_accuracy_score(y_test, pred),
            "rappel": recall_score(y_test, pred),
            "précision": precision_score(y_test, pred)}

lignes = [ligne("Persistance", base_pred.astype(float), base_pred)]
lignes += [ligne(nom, probas[nom], preds_f1[nom]) for nom in modeles]
print("\n=== Comparaison (seuil de décision = F1 max) ===")
print(pd.DataFrame(lignes).set_index("modèle").round(3))

# ---------- 6. Rappel par délai avant la chute ----------
bins = [0, 1.5, 3, 6, HORIZON_H + 0.01]
etiq = ["0-1.5h", "1.5-3h", "3-6h", f"6-{HORIZON_H}h"]

def rappel_par_delai(preds, titre):
    t = test[["y", "delai_h"]].copy()
    for nom, pr in preds.items():
        t[nom] = pr
    t = t[t["y"] == 1].copy()
    t["tranche"] = pd.cut(t["delai_h"], bins, labels=etiq)
    res = t.groupby("tranche", observed=True)[list(preds)].mean().round(3)
    res.insert(0, "n chutes", t.groupby("tranche", observed=True).size())
    print(f"\n=== {titre} ===")
    print(res)

rappel_par_delai(preds_f1, "Rappel par délai (seuil F1 max)")
rappel_par_delai(preds_prec, f"Rappel par délai (seuil réglé pour précision >= {PRECISION_CIBLE:.0%})")

# ---------- 7. Détail du meilleur modèle : Random Forest (+phys) ----------
nom_best = "Random Forest (+phys)"
y_pred = preds_f1[nom_best]
print(f"\n=== Détail : {nom_best} ===")
print(classification_report(y_test, y_pred, target_names=["OK", f"perf < {SEUIL_PERF}"]))

cm = confusion_matrix(y_test, y_pred)
tn, fp, fn, tp = cm.ravel()
print("Matrice de confusion (lignes = réel, colonnes = prédit)")
print(pd.DataFrame(cm, index=["réel OK", "réel < seuil"],
                   columns=["prédit OK", "prédit < seuil"]))
print(f"Fausses alertes : {fp}   Chutes ratées : {fn}")

fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
labels = ["OK", f"perf < {SEUIL_PERF}"]
ConfusionMatrixDisplay(cm, display_labels=labels).plot(ax=axes[0], cmap="Blues", colorbar=False)
axes[0].set_title("Effectifs")
ConfusionMatrixDisplay.from_predictions(
    y_test, y_pred, normalize="true", display_labels=labels,
    ax=axes[1], cmap="Blues", colorbar=False, values_format=".1%")
axes[1].set_title("Normalisée par classe réelle")
plt.suptitle(nom_best)
plt.tight_layout()
plt.savefig("matrice_confusion.png", dpi=150)
plt.show()

plt.figure(figsize=(6.5, 4.8))
for nom, p in probas.items():
    pr, rc, _ = precision_recall_curve(y_test, p)
    plt.plot(rc, pr, label=nom)
plt.xlabel("Rappel"); plt.ylabel("Précision")
plt.title("Courbes précision-rappel"); plt.legend(fontsize=8); plt.grid(alpha=0.3)
plt.tight_layout()
plt.savefig("comparaison_pr.png", dpi=150)
plt.show()

rf_best = modeles[nom_best][0]
imp = pd.Series(rf_best.feature_importances_, index=feats_all).sort_values(ascending=False)
print("\nTop 20 des variables (Random Forest +phys) :")
print(imp.head(20).round(4))
print(f"Part d'importance des tendances physiques (phy_*) : "
      f"{imp[[c for c in imp.index if c.startswith('phy_')]].sum():.1%}")

# ---------- 8. Validation par équipement (Random Forest +phys) ----------
n_eq = data["equipement_id"].nunique()
print(f"\nNombre d'équipements : {n_eq}")
if n_eq >= 2:
    gkf = GroupKFold(n_splits=min(5, n_eq))
    Xa = data[feats_all].fillna(mediane)
    ya = data["y"].astype(int)
    scores = []
    for tr, te in gkf.split(Xa, ya, groups=data["equipement_id"]):
        m = RandomForestClassifier(
            n_estimators=100, max_depth=12, min_samples_leaf=20, max_samples=0.3,
            class_weight="balanced_subsample", n_jobs=-1, random_state=42,
        ).fit(Xa.iloc[tr], ya.iloc[tr])
        eq_test = data["equipement_id"].iloc[te].unique()
        s = balanced_accuracy_score(ya.iloc[te], m.predict(Xa.iloc[te]))
        scores.append(s)
        print(f"  équipements testés {list(eq_test)[:5]}{'...' if len(eq_test) > 5 else ''} -> {s:.3f}")
    print("Balanced accuracy par équipement :", np.round(scores, 3),
          "moyenne", round(np.mean(scores), 3))