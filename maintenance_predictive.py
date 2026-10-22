import sys
from collections import defaultdict
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score

# ============================== CONFIG ==============================
STAR_DB = (Path(sys.argv[1]) if len(sys.argv) > 1
           else Path(__file__).resolve().parent / "data" / "base_etoile.duckdb")

PAS_MIN = 5
HORIZON_H = 9
H = HORIZON_H * 60 // PAS_MIN    # 108 pas
PERIODE = 90 // PAS_MIN          # 18 pas par orbite
JOUR = 24 * 60 // PAS_MIN        # 288 pas
SEUIL_PERF = 0.45                # seuil sur la performance AU SOLEIL
PHASE_SOLEIL = None              # None = détection auto

SILENCE_H = 9
S = SILENCE_H * 60 // PAS_MIN

COUT_FN = 2500                   # dégradation non anticipée
COUT_FP = 150                    # inspection inutile
COUT_TP = 300                    # dégradation correctement anticipée

GAP_MIN = HORIZON_H * 60
KINDS = ["corrective", "calibration", "inspection", "preventive", "remplacement"]
CAP_DEPUIS = 10 * JOUR

N_SEG = 8
PREMIER_BLOC = 3
N_PLIS_EQ = 5
N_ARBRES = 200
N_BOOT = 3000
LISSAGE = 5

RULES = {"Règle perf_min_18": "perf_min_18",
         "Règle perf_min_54": "perf_min_54",
         "Règle perf_min_108": "perf_min_108"}
LR_FEATS = ["perf_min_108", "perf_min_18", "perf_mean_54", "perf_std_108", "ratio_vie"]
REGLEE = "Règle réglée (fenêtre + seuil)"
BASELINE = "Baseline (facteur 1.15, non réglée)"
MODELES = [*RULES, "Logistique (5 var.)", "RF complet"]
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
        CASE WHEN f.maintenance_corrective_id   IS NOT NULL THEN 1.0 ELSE 0.0 END AS m_corrective,
        CASE WHEN f.maintenance_calibration_id  IS NOT NULL THEN 1.0 ELSE 0.0 END AS m_calibration,
        CASE WHEN f.maintenance_inspection_id   IS NOT NULL THEN 1.0 ELSE 0.0 END AS m_inspection,
        CASE WHEN f.maintenance_preventive_id   IS NOT NULL THEN 1.0 ELSE 0.0 END AS m_preventive,
        CASE WHEN f.maintenance_remplacement_id IS NOT NULL THEN 1.0 ELSE 0.0 END AS m_remplacement,
        (COALESCE(m_cor.duree, 0) + COALESCE(m_cal.duree, 0) + COALESCE(m_ins.duree, 0)
         + COALESCE(m_pre.duree, 0) + COALESCE(m_rem.duree, 0))::DOUBLE            AS m_duree
    FROM fait_production f
    JOIN dim_temps t       ON t.temps_id = f.temps_id
    JOIN dim_equipement e  ON e.equipement_id = f.equipement_id
    LEFT JOIN dim_alarme a          ON a.alarme_id = f.alarme_id
    LEFT JOIN dim_maintenance m_cor ON m_cor.maintenance_id = f.maintenance_corrective_id
    LEFT JOIN dim_maintenance m_cal ON m_cal.maintenance_id = f.maintenance_calibration_id
    LEFT JOIN dim_maintenance m_ins ON m_ins.maintenance_id = f.maintenance_inspection_id
    LEFT JOIN dim_maintenance m_pre ON m_pre.maintenance_id = f.maintenance_preventive_id
    LEFT JOIN dim_maintenance m_rem ON m_rem.maintenance_id = f.maintenance_remplacement_id
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

    sous = (p_soleil < SEUIL_PERF)
    deja = sous.astype(float).shift(1).rolling(S, min_periods=S).max()
    g["debut_evt"] = (sous & (deja == 0)).astype(float)
    g["sain"] = ((deja == 0) & (ps >= SEUIL_PERF)).astype(float)
    g["y"] = g["debut_evt"][::-1].rolling(H, min_periods=1).max()[::-1].shift(-1)

    for lag in [1, 2, 3, PERIODE // 2, PERIODE, 2 * PERIODE, 6 * PERIODE]:
        g[f"perf_lag_{lag}"] = ps.shift(lag)
    for w in [PERIODE, 3 * PERIODE, 6 * PERIODE]:
        g[f"perf_mean_{w}"] = p_soleil.rolling(w, min_periods=3).mean()
        g[f"perf_std_{w}"] = p_soleil.rolling(w, min_periods=3).std()
        g[f"perf_min_{w}"] = p_soleil.rolling(w, min_periods=3).min()
    g["perf_tendance"] = p_soleil.rolling(6 * PERIODE, min_periods=10).mean().diff(PERIODE)
    g["dist_seuil"] = ps - SEUIL_PERF

    g["ecart_temp"] = g["temperature_c"] - g["temp_amb"]
    for c in ["temperature_c", "tension_v", "courant_a", "puissance_w"]:
        g[f"{c}_mean_{PERIODE}"] = g[c].rolling(PERIODE).mean()
        g[f"{c}_diff"] = g[c].diff()

    alarme = g["severite"].notna().astype(float)
    g["nb_alarmes_1orb"] = alarme.rolling(PERIODE).sum()
    g["nb_alarmes_6orb"] = alarme.rolling(6 * PERIODE).sum()

    pos = np.arange(len(g))
    for k in KINDS:
        f = g[f"m_{k}"]
        g[f"maint_{k}_6orb"] = f.rolling(6 * PERIODE, min_periods=1).sum()
        g[f"maint_{k}_1j"] = f.rolling(JOUR, min_periods=1).sum()
        dernier = pd.Series(np.where(f.values > 0, pos, np.nan), index=g.index).ffill()
        g[f"depuis_maint_{k}"] = (pos - dernier).fillna(CAP_DEPUIS)
    g["en_maintenance"] = (g[[f"m_{k}" for k in KINDS]].sum(axis=1) > 0).astype(float)
    g["maint_duree_1j"] = g["m_duree"].rolling(JOUR, min_periods=1).sum()
    g = g.drop(columns=[f"m_{k}" for k in KINDS] + ["m_duree"])

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
data = data.dropna(subset=["y"])
data = data[data["sain"] == 1].drop(columns="sain").sort_index()
print(f"Lignes éligibles : {len(data)}   positives : {int(data['y'].sum())} ({data['y'].mean():.2%})")

feats = [c for c in data.columns if c not in ("y", "equipement_id", "debut_evt")]
TM = en_minutes(data.index)
EQS = sorted(data["equipement_id"].unique())
gap = pd.Timedelta(minutes=GAP_MIN)

# ---------- 3. Simulation du coût par épisode ----------
def preparer(d, score, ev):
    fr = pd.DataFrame({"eq": d["equipement_id"].values, "t": en_minutes(d.index), "p": np.asarray(score)})
    fr = fr.sort_values(["eq", "t"])
    groupes = {eq: (g["t"].to_numpy(), g["p"].to_numpy()) for eq, g in fr.groupby("eq")}
    t_min, t_max = int(fr["t"].min()), int(fr["t"].max())
    evp = {eq: a[(a >= t_min + GAP_MIN) & (a <= t_max)] for eq, a in ev.items() if eq in groupes}
    return {"groupes": groupes, "ev": evp, "t_eval_fin": t_max - GAP_MIN}

def alertes(t_cand):
    out, i, n = [], 0, len(t_cand)
    while i < n:
        out.append(t_cand[i])
        i = int(np.searchsorted(t_cand, t_cand[i] + GAP_MIN, side="left"))
    return np.array(out, dtype="int64")

def simuler(ctx, seuil):
    tot = defaultdict(int); par_eq = {}; leads = []
    for eq in set(ctx["groupes"]) | set(ctx["ev"]):
        ons = ctx["ev"].get(eq, np.array([], dtype="int64"))
        tp = fp = man = cov_n = 0
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
            tp = int(est_tp.sum())
            fp = int((~est_tp & (al <= ctx["t_eval_fin"])).sum())
        if len(ons):
            if len(al):
                j = np.searchsorted(al, ons - GAP_MIN, side="left")
                cov = (j < len(al)) & (al[np.minimum(j, len(al) - 1)] < ons)
                cov_n = int(cov.sum()); man = int((~cov).sum())
                leads.extend((ons[cov] - al[j[cov]]).tolist())
            else:
                man = len(ons)
        par_eq[eq] = tp * COUT_TP + fp * COUT_FP + man * COUT_FN
        tot["tp"] += tp; tot["fp"] += fp; tot["manques"] += man; tot["couverts"] += cov_n
    return {**tot, "par_eq": par_eq, "leads": leads}

def res_jamais(ctx):
    n = sum(len(a) for a in ctx["ev"].values())
    return {"tp": 0, "fp": 0, "manques": n, "couverts": 0,
            "par_eq": {eq: len(a) * COUT_FN for eq, a in ctx["ev"].items()}, "leads": []}

def res_oracle(ctx):
    n = sum(len(a) for a in ctx["ev"].values())
    return {"tp": n, "fp": 0, "manques": 0, "couverts": n,
            "par_eq": {eq: len(a) * COUT_TP for eq, a in ctx["ev"].items()}, "leads": []}

GRILLE = np.append(np.round(np.arange(0.01, 1.00, 0.01), 2), 1.01)   # 1.01 = ne jamais alerter

def cout_total(ctx, s):
    return sum(simuler(ctx, s)["par_eq"].values())

def choisir_seuil(courbe):
    """Seuil minimisant la courbe de coût lissée ; renvoie (seuil, coût de validation à ce seuil)."""
    c = np.asarray(courbe, dtype=float)
    sm = pd.Series(c[:-1]).rolling(LISSAGE, center=True, min_periods=1).mean().to_numpy()
    c2 = np.append(sm, c[-1])                       # « ne jamais alerter » n'est pas lissé
    i = int(np.argmin(c2))
    return float(GRILLE[i]), float(c2[i])

# ---------- 4. Modèles ----------
def run_modele(nom, tr_all, te):
    cut_val = tr_all.index[int(len(tr_all) * 0.8)]
    fit = tr_all[tr_all.index < cut_val - gap]
    val = tr_all[tr_all.index >= cut_val]
    if len(fit) == 0 or len(val) == 0:
        return None
    if nom in RULES:
        col = RULES[nom]                              # score = 1 - perf_min : plus c'est haut, plus le risque est élevé
        s_val = 1 - val[col].fillna(1).to_numpy()
        s_te = 1 - te[col].fillna(1).to_numpy()
    else:
        if fit["y"].nunique() < 2:
            return None
        if nom.startswith("Logistique"):
            fl = LR_FEATS
            mdl = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced"))
        else:
            fl = feats
            mdl = RandomForestClassifier(
                n_estimators=N_ARBRES, max_depth=8, min_samples_leaf=50, max_samples=0.5,
                class_weight="balanced_subsample", n_jobs=-1, random_state=42)
        med = fit[fl].median()
        mdl.fit(fit[fl].fillna(med), fit["y"].astype(int))
        s_val = mdl.predict_proba(val[fl].fillna(med))[:, 1]
        s_te = mdl.predict_proba(te[fl].fillna(med))[:, 1]
    ctx_val = preparer(val, s_val, EV)
    couts_val = np.array([cout_total(ctx_val, s) for s in GRILLE], dtype=float)
    return {"couts_val": couts_val, "ctx_te": preparer(te, s_te, EV), "s_te": s_te}

# ---------- 5. Agrégation ----------
def nouvel_agg():
    return {"tp": 0, "fp": 0, "manques": 0, "couverts": 0,
            "leads": [], "par_eq": defaultdict(float)}

def ajouter(agg, res):
    for k in ("tp", "fp", "manques", "couverts"):
        agg[k] += res[k]
    agg["leads"].extend(res["leads"])
    for eq, c in res["par_eq"].items():
        agg["par_eq"][eq] += c

def evaluer_schema(nom_schema, plis, cumulatif):
    noms = ["Jamais inspecter", "Inspection périodique (9 h)", BASELINE, *MODELES, REGLEE, "Oracle (parfait)"]
    agg = {n: nouvel_agg() for n in noms}
    hist = {n: [] for n in MODELES}
    y_all = {n: [] for n in MODELES}; s_all = {n: [] for n in MODELES}
    detail = []
    for i, (tr_all, te) in enumerate(plis, 1):
        if len(te) == 0 or len(tr_all) < 1000:
            print(f"  pli {i} ignoré (données insuffisantes)"); continue
        ctx0 = preparer(te, np.zeros(len(te)), EV)
        r_jam = res_jamais(ctx0); n_ev = r_jam["manques"]
        if n_ev == 0:
            print(f"  pli {i} ignoré (aucun épisode évaluable)"); continue
        outs = {nom: run_modele(nom, tr_all, te) for nom in MODELES}
        if any(o is None for o in outs.values()):
            print(f"  pli {i} ignoré (un modèle n'a pas pu être entraîné)"); continue

        ajouter(agg["Jamais inspecter"], r_jam)
        ajouter(agg["Oracle (parfait)"], res_oracle(ctx0))
        ajouter(agg["Inspection périodique (9 h)"], simuler(ctx0, 0.0))
        base = (te["perf_min_108"].fillna(1) < SEUIL_PERF * 1.15).astype(float).to_numpy()
        r_base = simuler(preparer(te, base, EV), 0.5)
        ajouter(agg[BASELINE], r_base)
        ligne = {"pli": i, "épisodes": n_ev,
                 "jamais (€)": sum(r_jam["par_eq"].values()),
                 BASELINE.split(" (")[0] + " (€)": sum(r_base["par_eq"].values())}

        for nom, out in outs.items():
            hist[nom].append(out["couts_val"])
            courbe = np.sum(hist[nom], axis=0) if cumulatif else out["couts_val"]
            out["seuil"], out["cout_val"] = choisir_seuil(courbe)
            res = simuler(out["ctx_te"], out["seuil"])
            ajouter(agg[nom], res)
            ligne[f"{nom} (€)"] = sum(res["par_eq"].values())
            y_all[nom].append(te["y"].astype(int).to_numpy()); s_all[nom].append(out["s_te"])

        # Règle réglée : la fenêtre est choisie sur la validation (coût de validation le plus bas)
        meilleure = min(RULES, key=lambda n: outs[n]["cout_val"])
        res = simuler(outs[meilleure]["ctx_te"], outs[meilleure]["seuil"])
        ajouter(agg[REGLEE], res)
        ligne[REGLEE.split(" (")[0] + " (€)"] = sum(res["par_eq"].values())
        ligne["fenêtre choisie"] = RULES[meilleure]
        ligne["seuil règle"] = outs[meilleure]["seuil"]
        detail.append(ligne)
        print(f"  pli {i} terminé ({n_ev} épisodes)")

    print(f"\n--- Détail par pli : {nom_schema} ---")
    print(pd.DataFrame(detail).round(2).to_string(index=False))
    aucs = {}
    for nom in MODELES:
        if y_all[nom]:
            y, s = np.concatenate(y_all[nom]), np.concatenate(s_all[nom])
            aucs[nom] = (roc_auc_score(y, s), average_precision_score(y, s))
    return agg, aucs

def tableau(agg):
    c_jam = sum(agg["Jamais inspecter"]["par_eq"].values())
    lignes = []
    for nom, a in agg.items():
        cout = sum(a["par_eq"].values()); n_ev = a["couverts"] + a["manques"]
        lignes.append({
            "stratégie": nom, "coût total (€)": cout,
            "économie vs jamais (%)": (1 - cout / c_jam) * 100 if c_jam else np.nan,
            "alertes OK": a["tp"], "fausses alertes": a["fp"], "manqués": a["manques"],
            "rappel": a["couverts"] / n_ev if n_ev else np.nan,
            "précision": a["tp"] / (a["tp"] + a["fp"]) if (a["tp"] + a["fp"]) else np.nan,
            "anticip. méd. (h)": np.median(a["leads"]) / 60 if a["leads"] else np.nan})
    return pd.DataFrame(lignes).set_index("stratégie")

def bootstrap(agg):
    rng = np.random.default_rng(0)
    vec = {n: np.array([a["par_eq"].get(eq, 0.0) for eq in EQS]) for n, a in agg.items()}
    idx = rng.integers(0, len(EQS), size=(N_BOOT, len(EQS)))
    paires = [(REGLEE, "Jamais inspecter"), (REGLEE, BASELINE),
              ("Logistique (5 var.)", REGLEE), ("RF complet", REGLEE),
              ("Logistique (5 var.)", "RF complet")]
    print("\nÉcarts de coût (A − B), IC95 % par bootstrap sur les équipements (négatif = A moins cher) :")
    for a, b in paires:
        diff = vec[a][idx].sum(axis=1) - vec[b][idx].sum(axis=1)
        lo, hi = np.percentile(diff, [2.5, 97.5])
        print(f"  {a}  −  {b}\n      moyenne {diff.mean():+,.0f} €   IC95 [{lo:+,.0f} ; {hi:+,.0f}] €   "
              f"A moins cher dans {np.mean(diff < 0):.0%} des tirages")

pd.set_option("display.width", 260); pd.set_option("display.max_columns", 40)

# ---------- 6A. Validation temporelle en blocs ----------
print("\n================ VALIDATION TEMPORELLE EN BLOCS ================")
bornes = np.linspace(TM.min(), TM.max(), N_SEG + 1)
plis_t = []
for k in range(PREMIER_BLOC, N_SEG):
    lo, hi = bornes[k], bornes[k + 1]
    masque_te = (TM >= lo) & ((TM < hi) if k < N_SEG - 1 else (TM <= hi))
    plis_t.append((data[TM < lo - GAP_MIN], data[masque_te]))
agg_t, auc_t = evaluer_schema("blocs temporels (seuil cumulatif lissé)", plis_t, cumulatif=True)
print("\n=== Total sur tous les blocs de test ===")
print(tableau(agg_t).round(3).to_string())
print("\nPouvoir de classement (toutes lignes saines) :")
for nom, (roc, pr) in auc_t.items():
    print(f"  {nom:<26} ROC-AUC {roc:.3f}   PR-AUC {pr:.3f}")
bootstrap(agg_t)

# ---------- 6B. Validation par équipement ----------
print("\n================ VALIDATION PAR ÉQUIPEMENT (équipements jamais vus) ================")
gkf = GroupKFold(n_splits=min(N_PLIS_EQ, len(EQS)))
plis_e = [(data.iloc[a], data.iloc[b]) for a, b in gkf.split(data, groups=data["equipement_id"])]
agg_e, auc_e = evaluer_schema("par équipement (seuil du pli lissé)", plis_e, cumulatif=False)
print("\n=== Total sur tous les plis ===")
print(tableau(agg_e).round(3).to_string())
print("\nPouvoir de classement (toutes lignes saines) :")
for nom, (roc, pr) in auc_e.items():
    print(f"  {nom:<26} ROC-AUC {roc:.3f}   PR-AUC {pr:.3f}")
bootstrap(agg_e)

print("\nLecture : un modèle est préférable à un autre seulement si l'IC95 de leur écart de coût ne contient pas 0.")