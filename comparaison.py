import sys
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score, average_precision_score

# ============================== CONFIG ==============================
STAR_DB = (Path(sys.argv[1]) if len(sys.argv) > 1
           else Path(__file__).resolve().parent / "data" / "base_etoile.duckdb")

PAS_MIN = 5
HORIZON_H = 9
H = HORIZON_H * 60 // PAS_MIN    # 108 pas
PERIODE = 90 // PAS_MIN          # 18 pas par orbite
SEUIL_PERF = 0.45                # seuil sur la performance AU SOLEIL
PHASE_SOLEIL = None              # None = détection auto

# Un "nouvel épisode" = première mesure sous le seuil après SILENCE_H heures sans mesure sous le seuil.
# Les lignes évaluées sont celles où l'équipement est SAIN depuis au moins SILENCE_H heures.
# Mettez 1.5 ou 3 pour compter aussi les rechutes rapides comme des épisodes.
SILENCE_H = 9
S = SILENCE_H * 60 // PAS_MIN

COUT_FN = 2500                   # dégradation non anticipée
COUT_FP = 150                    # inspection inutile
COUT_TP = 300                    # dégradation correctement anticipée
COUT_TN = 0                      # pas de dégradation / pas d'inspection

GAP_MIN = HORIZON_H * 60         # horizon d'anticipation et délai de silence après alerte (minutes)
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

if PHASE_SOLEIL is None:
    PHASE_SOLEIL = df.groupby("phase")["performance"].mean().idxmax()
print(f"Phase ensoleillée retenue : {PHASE_SOLEIL!r}")

# ---------- 2. Épisodes, cible et variables ----------
SIGNAUX_PHYSIQUES = ["temperature_c", "tension_v", "courant_a",
                     "puissance_w", "ray_solaire", "ecart_temp"]

def construire(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("ts").drop_duplicates("ts").set_index("ts").copy()
    au_soleil = (g["phase"] == PHASE_SOLEIL)
    p = g["performance"]
    p_soleil = p.where(au_soleil)
    ps = p_soleil.ffill(limit=PERIODE)

    # Début d'épisode : mesure sous le seuil, sans aucune mesure sous le seuil sur les S pas précédents
    sous = (p_soleil < SEUIL_PERF)
    deja = sous.astype(float).shift(1).rolling(S, min_periods=S).max()   # NaN au début de série
    g["debut_evt"] = (sous & (deja == 0)).astype(float)

    # Ligne éligible : équipement SAIN (rien sous le seuil depuis S pas, et dernière perf >= seuil)
    g["sain"] = ((deja == 0) & (ps >= SEUIL_PERF)).astype(float)

    # Cible : un NOUVEL épisode commence dans les H prochains pas
    g["y"] = g["debut_evt"][::-1].rolling(H, min_periods=1).max()[::-1].shift(-1)

    # Historique de performance
    for lag in [1, 2, 3, PERIODE // 2, PERIODE, 2 * PERIODE, 6 * PERIODE]:
        g[f"perf_lag_{lag}"] = ps.shift(lag)
    for w in [PERIODE, 3 * PERIODE, 6 * PERIODE]:
        g[f"perf_mean_{w}"] = p_soleil.rolling(w, min_periods=3).mean()
        g[f"perf_std_{w}"] = p_soleil.rolling(w, min_periods=3).std()
        g[f"perf_min_{w}"] = p_soleil.rolling(w, min_periods=3).min()
    g["perf_tendance"] = p_soleil.rolling(6 * PERIODE, min_periods=10).mean().diff(PERIODE)
    g["dist_seuil"] = ps - SEUIL_PERF

    # Signaux physiques
    g["ecart_temp"] = g["temperature_c"] - g["temp_amb"]
    for c in ["temperature_c", "tension_v", "courant_a", "puissance_w"]:
        g[f"{c}_mean_{PERIODE}"] = g[c].rolling(PERIODE).mean()
        g[f"{c}_diff"] = g[c].diff()

    # Alarmes et maintenance (historique)
    alarme = g["severite"].notna().astype(float)
    g["nb_alarmes_1orb"] = alarme.rolling(PERIODE).sum()
    g["nb_alarmes_6orb"] = alarme.rolling(6 * PERIODE).sum()
    g["en_maintenance"] = g["type_maintenance"].notna().astype(float)
    g["maint_6orb"] = g["en_maintenance"].rolling(6 * PERIODE).sum()

    # Tendances physiques au soleil (préfixe phy_)
    for c in SIGNAUX_PHYSIQUES:
        s = g[c].where(au_soleil)
        for w in [3 * PERIODE, 6 * PERIODE]:
            m = s.rolling(w, min_periods=10).mean()
            g[f"phy_{c}_m{w}"] = m
            g[f"phy_{c}_std{w}"] = s.rolling(w, min_periods=10).std()
            g[f"phy_{c}_pente{w}"] = m.diff(w)
    g["phy_perf_pente_54"] = p_soleil.rolling(3 * PERIODE, min_periods=10).mean().diff(3 * PERIODE)
    g["phy_perf_pente_108"] = p_soleil.rolling(6 * PERIODE, min_periods=10).mean().diff(6 * PERIODE)
    return g

data = pd.concat([construire(g) for _, g in df.groupby("equipement_id")])

EPOCH = pd.Timestamp("2000-01-01")
def en_minutes(index):
    return np.round(((pd.DatetimeIndex(index) - EPOCH) / pd.Timedelta(minutes=1)).to_numpy()).astype("int64")

evts = data.loc[data["debut_evt"] == 1, ["equipement_id"]].copy()
evts["t"] = en_minutes(evts.index)
EV = {eq: np.sort(g["t"].to_numpy()) for eq, g in evts.groupby("equipement_id")}
print(f"Épisodes (silence = {SILENCE_H} h) : {len(evts)} sur {data['equipement_id'].nunique()} équipements")

data = pd.get_dummies(data, columns=["phase", "type_equipement", "severite"],
                      dtype=float, drop_first=True)
data = data.drop(columns=["type_maintenance"]).dropna(subset=["y"])
n_avant = len(data)
data = data[data["sain"] == 1].drop(columns="sain").sort_index()
print(f"Lignes éligibles (équipement sain depuis {SILENCE_H} h) : {len(data)} / {n_avant}")

# ---------- 3. Découpage temporel : fit / validation / test ----------
gap = pd.Timedelta(minutes=GAP_MIN)
cut_test = data.index[int(len(data) * 0.8)]
train_all = data[data.index < cut_test - gap]
test = data[data.index >= cut_test]
cut_val = train_all.index[int(len(train_all) * 0.8)]
fit = train_all[train_all.index < cut_val - gap]
val = train_all[train_all.index >= cut_val]

feats = [c for c in data.columns if c not in ("y", "equipement_id", "debut_evt")]
mediane = fit[feats].median()
Xfit, Xval, Xte = (d[feats].fillna(mediane) for d in (fit, val, test))
yfit, yval, yte = (d["y"].astype(int) for d in (fit, val, test))

print(f"Lignes fit : {len(fit)}   validation : {len(val)}   test : {len(test)}")
print(f"Lignes positives  fit : {yfit.sum()} ({yfit.mean():.2%})   "
      f"val : {yval.sum()} ({yval.mean():.2%})   test : {yte.sum()} ({yte.mean():.2%})")
if min(yfit.nunique(), yval.nunique(), yte.nunique()) < 2:
    raise SystemExit("Une seule classe dans un des jeux : essayez SILENCE_H plus petit.")

# ---------- 4. Modèle (entraîné uniquement sur fit) ----------
rf = RandomForestClassifier(
    n_estimators=300, max_depth=8, min_samples_leaf=50, max_samples=0.5,
    class_weight="balanced_subsample", n_jobs=-1, random_state=42)
rf.fit(Xfit, yfit)
p_val = rf.predict_proba(Xval)[:, 1]
p_te = rf.predict_proba(Xte)[:, 1]
print(f"\nSur lignes saines -> ROC-AUC test : {roc_auc_score(yte, p_te):.3f}   "
      f"PR-AUC test : {average_precision_score(yte, p_te):.3f}   "
      f"(référence PR-AUC = taux positif {yte.mean():.3f})")

# ---------- 5. Simulation du coût par épisode ----------
def preparer(d, proba, ev):
    fr = pd.DataFrame({"eq": d["equipement_id"].values, "t": en_minutes(d.index), "p": np.asarray(proba)})
    fr = fr.sort_values(["eq", "t"])
    groupes = {eq: (g["t"].to_numpy(), g["p"].to_numpy()) for eq, g in fr.groupby("eq")}
    t_min, t_max = int(fr["t"].min()), int(fr["t"].max())
    evp = {eq: a[(a >= t_min + GAP_MIN) & (a <= t_max)] for eq, a in ev.items()}
    return {"groupes": groupes, "ev": evp, "t_eval_fin": t_max - GAP_MIN,
            "jours": (t_max - t_min) / 1440, "n_eq": len(groupes)}

def alertes(t_cand):
    out, i, n = [], 0, len(t_cand)
    while i < n:
        out.append(t_cand[i])
        i = int(np.searchsorted(t_cand, t_cand[i] + GAP_MIN, side="left"))
    return np.array(out, dtype="int64")

def simuler(ctx, seuil):
    tp = fp = manques = couverts = 0
    leads = []
    for eq in set(ctx["groupes"]) | set(ctx["ev"]):
        ons = ctx["ev"].get(eq, np.array([], dtype="int64"))
        if eq in ctx["groupes"]:
            t, p = ctx["groupes"][eq]
            al = alertes(t[p >= seuil])
        else:
            al = np.array([], dtype="int64")
        if len(al):
            if len(ons):
                i = np.searchsorted(ons, al, side="right")
                est_tp = (i < len(ons)) & (ons[np.minimum(i, len(ons) - 1)] <= al + GAP_MIN)
            else:
                est_tp = np.zeros(len(al), dtype=bool)
            tp += int(est_tp.sum())
            fp += int((~est_tp & (al <= ctx["t_eval_fin"])).sum())
        if len(ons):
            if len(al):
                j = np.searchsorted(al, ons - GAP_MIN, side="left")
                cov = (j < len(al)) & (al[np.minimum(j, len(al) - 1)] < ons)
                couverts += int(cov.sum()); manques += int((~cov).sum())
                leads.extend((ons[cov] - al[j[cov]]).tolist())
            else:
                manques += len(ons)
    n_ev = couverts + manques
    return {"cout": tp * COUT_TP + fp * COUT_FP + manques * COUT_FN,
            "tp": tp, "fp": fp, "manques": manques, "n_ev": n_ev,
            "rappel_ev": couverts / n_ev if n_ev else np.nan,
            "precision_al": tp / (tp + fp) if (tp + fp) else np.nan,
            "fa_par_eq_jour": fp / (ctx["n_eq"] * ctx["jours"]) if ctx["jours"] > 0 else np.nan,
            "lead_med_h": np.median(leads) / 60 if leads else np.nan}

ctx_val = preparer(val, p_val, EV)
ctx_te = preparer(test, p_te, EV)
n_ev_val = sum(len(a) for a in ctx_val["ev"].values())
n_ev_te = sum(len(a) for a in ctx_te["ev"].values())
print(f"\nÉpisodes évaluables : validation = {n_ev_val}   test = {n_ev_te}")
print(f"Précision minimale pour qu'une alerte soit rentable : "
      f"{COUT_FP / (COUT_FP + COUT_FN - COUT_TP):.1%}")

# ---------- ÉTAPE 1 : seuil sur la VALIDATION (la grille inclut « ne jamais alerter » = 1.01) ----------
grille = np.append(np.round(np.arange(0.01, 1.00, 0.01), 2), 1.01)
couts_val = np.array([simuler(ctx_val, s)["cout"] for s in grille])
seuil_ep = float(grille[int(np.argmin(couts_val))])
if seuil_ep > 1:
    print("Sur la validation, la meilleure stratégie est de NE JAMAIS alerter (seuil = 1.01).")
else:
    print(f"Seuil optimal (validation, coût par épisode) : {seuil_ep:.2f}")
print(f"Coût validation : {couts_val.min():,.0f} €   (jamais inspecter : {n_ev_val * COUT_FN:,.0f} €)")

plt.figure(figsize=(6.5, 4.5))
plt.plot(grille[:-1], couts_val[:-1] / 1000, label="coût RF (validation)")
plt.axhline(n_ev_val * COUT_FN / 1000, color="gray", ls=":", label="jamais inspecter")
plt.axvline(min(seuil_ep, 1.0), color="red", ls="--", label=f"seuil retenu = {seuil_ep:.2f}")
plt.xlabel("Seuil de décision"); plt.ylabel("Coût (k€)")
plt.title("Coût par épisode en fonction du seuil (validation)")
plt.legend(); plt.grid(alpha=0.3); plt.tight_layout()
plt.savefig("cout_episode_vs_seuil_validation.png", dpi=150); plt.close()

# ---------- ÉTAPE 2 : application UNIQUE au test ----------
base_score = (test["perf_min_108"].fillna(1) < SEUIL_PERF * 1.15).astype(float).to_numpy()
ctx_base = preparer(test, base_score, EV)

lignes = {
    "Jamais inspecter": {"cout": n_ev_te * COUT_FN, "tp": 0, "fp": 0, "manques": n_ev_te, "n_ev": n_ev_te,
                         "rappel_ev": 0.0, "precision_al": np.nan, "fa_par_eq_jour": 0.0, "lead_med_h": np.nan},
    "Inspection périodique (9 h)": simuler(ctx_te, 0.0),
    "Marge faible (baseline)": simuler(ctx_base, 0.5),
    f"Random Forest (seuil {seuil_ep:.2f})": simuler(ctx_te, seuil_ep),
    "Oracle (parfait)": {"cout": n_ev_te * COUT_TP, "tp": n_ev_te, "fp": 0, "manques": 0, "n_ev": n_ev_te,
                         "rappel_ev": 1.0, "precision_al": 1.0, "fa_par_eq_jour": 0.0, "lead_med_h": np.nan},
}
tab = pd.DataFrame(lignes).T
c_jamais, c_oracle = lignes["Jamais inspecter"]["cout"], lignes["Oracle (parfait)"]["cout"]
tab["économie vs jamais (%)"] = (1 - tab["cout"].astype(float) / c_jamais) * 100
tab = tab.rename(columns={"cout": "coût test (€)", "tp": "alertes OK", "fp": "fausses alertes",
                          "manques": "épisodes manqués", "n_ev": "épisodes",
                          "rappel_ev": "rappel épisodes", "precision_al": "précision alertes",
                          "fa_par_eq_jour": "fausses alertes/équip./jour",
                          "lead_med_h": "anticipation médiane (h)"})
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
print("\n=== Test : coût par épisode (seuil appliqué une seule fois) ===")
print(tab.round(3).to_string())
print("\nLecture : la stratégie est utile seulement si son coût est INFÉRIEUR à « Jamais inspecter ».")
print("Avec peu d'épisodes, chaque épisode de plus ou de moins déplace le coût de 2 200 à 2 500 € :"
      " interprétez avec prudence.")

# ---------- 6. Importance des variables ----------
imp = pd.Series(rf.feature_importances_, index=feats).sort_values(ascending=False)
print("\nTop 15 des variables (importance MDI) :")
print(imp.head(15).round(4).to_string())

def groupe(c):
    if c.startswith("phy_"): return "tendances physiques"
    if c.startswith("perf_") or c == "dist_seuil": return "historique de performance"
    if "alarme" in c or "maint" in c or c.startswith("severite"): return "alarmes / maintenance"
    if c in ("ratio_vie", "p_nominale", "rend_nominal") or c.startswith("type_equipement"):
        return "caractéristiques équipement / âge"
    return "signaux physiques instantanés / phase"

print("\nImportance par famille de variables :")
print(imp.groupby(groupe).sum().sort_values(ascending=False).map("{:.1%}".format).to_string())