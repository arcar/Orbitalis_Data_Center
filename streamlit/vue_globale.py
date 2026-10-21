from pathlib import Path

import duckdb 
import joblib 
import numpy as np 
import pandas as pd 
import plotly.express as px 
import streamlit as st

# ============================================================
# 1. CONFIGURATION
# ============================================================

st.set_page_config( page_title="Orbitalis Data Center",  layout="wide", )

BASE_DIR = Path(__file__).resolve().parent.parent 
DATA_DIR = BASE_DIR / "data"

DB_PATH = DATA_DIR / "base_etoile.duckdb" 
MODEL_PATH = DATA_DIR / "modele_random_forest.joblib" 
DECISIONS_PATH = DATA_DIR / "decisions.csv"

HORIZON_HEURES = 9

SEUIL_FAIBLE = 0.30 
SEUIL_ELEVE = 0.70

st.title("Orbitalis Data Center") 
st.caption("Pilotage intelligent de la maintenance photovoltaïque")

# ============================================================
# 2. VARIABLES ATTENDUES PAR LE MODELE
# ============================================================

VARIABLES_NUMERIQUES = [ "puissance_w", "temperature_c", "rayonnement", "tension_v", "courant_a", "performance", "perte_production", "rayonnement_solaire_w_m2", "temperature_ambiante_c", "puissance_nominale_w", "rendement_nominal", "duree_vie_annee", "ratio_vie_a_date", ]

VARIABLES_CATEGORIELLES = [ "type_equipement", "phase", "site", ]

VARIABLES_MODELE = ( VARIABLES_NUMERIQUES + VARIABLES_CATEGORIELLES )

# ============================================================
# 3. CHARGEMENT DE LA BASE DUCKDB
# ============================================================

@st.cache_data(ttl=60) 
def charger_donnees():

    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Base DuckDB introuvable : {DB_PATH}"
        )

    con = duckdb.connect(str(DB_PATH), read_only=True)

    try:
        df = con.execute("""
            SELECT
                f.*,

                STRPTIME(
                    CAST(f.temps_id AS VARCHAR),
                    '%Y%m%d%H%M'
                ) AS timestamp,

                t.date AS date_mesure,
                t.heure,
                t.minute,

                e.site,
                e.type_equipement,
                e.puissance_nominale_w,
                e.rendement_nominal,
                e.duree_vie_annee,
                e.date_installation,

                da.severite AS severite_alarme

            FROM fait_production AS f

            LEFT JOIN dim_temps AS t
                ON f.temps_id = t.temps_id

            LEFT JOIN dim_equipement AS e
                ON f.equipement_id = e.equipement_id

            LEFT JOIN dim_alarme AS da
                ON f.alarme_id = da.alarme_id

            WHERE f.temps_id IS NOT NULL
        """).fetchdf()

    finally:
        con.close()

    if df.empty:
        return df

    df["timestamp"] = pd.to_datetime(
        df["timestamp"], errors="coerce"
    )

    df["date_mesure"] = pd.to_datetime(
        df["date_mesure"], errors="coerce"
    )

    df["date_installation"] = pd.to_datetime(
        df["date_installation"], errors="coerce"
    )

    df = df.dropna(
        subset=["equipement_id", "timestamp"]
    ).copy()

    # Calcul de l'ancienneté de l'équipement à la date
    # de chaque observation, conformément à l'entraînement.
    age_jours = (
        df["timestamp"] - df["date_installation"]
    ).dt.total_seconds() / 86400

    duree_vie = pd.to_numeric(
        df["duree_vie_annee"], errors="coerce"
    ).clip(lower=1)

    df["ratio_vie_a_date"] = (
        age_jours.clip(lower=0)
        / (duree_vie * 365.25)
    )

    # On conserve une seule observation par équipement
    # et timestamp pour éviter les doubles comptages.
    df = (
        df.sort_values(["equipement_id", "timestamp"])
        .drop_duplicates(
            subset=["equipement_id", "timestamp"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    return df

# ============================================================
# 4. CHARGEMENT DU MODELE
# ============================================================

@st.cache_resource 
def charger_modele():
    if not MODEL_PATH.exists():
        return None

    return joblib.load(MODEL_PATH)

# ============================================================
# 5. CALCUL DES PREDICTIONS
# ============================================================

def calculer_predictions(df, modele):

    resultat = df.copy()

    resultat["score_risque"] = np.nan
    resultat["niveau_risque"] = "Indisponible"

    if modele is None or resultat.empty:
        return resultat

    # Dernière observation disponible pour chaque équipement.
    dernieres = (
        resultat.sort_values("timestamp")
        .drop_duplicates(
            subset=["equipement_id"],
            keep="last",
        )
        .copy()
    )

    # Reconstitution exacte des variables attendues.
    for colonne in VARIABLES_NUMERIQUES:

        if colonne not in dernieres.columns:
            dernieres[colonne] = np.nan

        dernieres[colonne] = pd.to_numeric(
            dernieres[colonne], errors="coerce"
        )

    for colonne in VARIABLES_CATEGORIELLES:

        if colonne not in dernieres.columns:
            dernieres[colonne] = pd.NA

        dernieres[colonne] = dernieres[colonne].astype(
            "string"
        )

    X = dernieres[VARIABLES_MODELE].copy()

    # Le pipeline applique lui-même imputation, normalisation
    # et encodage catégoriel.
    probabilites = modele.predict_proba(X)

    classes = list(modele.classes_)

    if 1 not in classes:
        raise ValueError(
            "Le modèle ne contient pas la classe positive 1."
        )

    indice_positif = classes.index(1)

    dernieres["score_risque"] = probabilites[
        :, indice_positif
    ]

    dernieres["niveau_risque"] = np.select(
        [
            dernieres["score_risque"] < SEUIL_FAIBLE,
            dernieres["score_risque"] < SEUIL_ELEVE,
        ],
        [
            "Faible",
            "Modéré",
        ],
        default="Élevé",
    )

    # Le score courant est associé à toutes les observations
    # du même équipement, sans créer un faux historique
    # de prédictions.
    scores = dernieres[
        [
            "equipement_id",
            "score_risque",
            "niveau_risque",
        ]
    ]

    resultat = resultat.drop(
        columns=["score_risque", "niveau_risque"],
        errors="ignore",
    )

    resultat = resultat.merge(
        scores,
        on="equipement_id",
        how="left",
    )

    resultat["niveau_risque"] = (
        resultat["niveau_risque"]
        .fillna("Indisponible")
    )

    return resultat

# ============================================================
# 6. CONSTRUCTION DES INDICATEURS PAR EQUIPEMENT
# ============================================================

def construire_indicateurs(df):

    if df.empty:
        return pd.DataFrame()

    df = df.sort_values(
        ["equipement_id", "timestamp"]
    ).copy()

    # Dernière observation de chaque équipement.
    dernieres = (
        df.drop_duplicates(
            subset=["equipement_id"],
            keep="last",
        )
        .copy()
    )

    # Indicateurs calculés sur l'ensemble des observations.
    indicateurs = (
        df.groupby("equipement_id", as_index=False)
        .agg(
            puissance_moyenne=("puissance_w", "mean"),
            nb_mesures=("temps_id", "count"),
        )
    )

    colonnes_dernieres = [
        "equipement_id",
        "site",
        "type_equipement",
        "performance",
        "timestamp",
        "score_risque",
        "niveau_risque",
    ]

    dernieres = dernieres[
        colonnes_dernieres
    ].rename(
        columns={
            "performance": "derniere_performance",
            "timestamp": "derniere_mesure",
        }
    )

    equipements = indicateurs.merge(
        dernieres,
        on="equipement_id",
        how="left",
    )

    # Nombre de mesures auxquelles une alarme est associée.
    if "alarme_id" in df.columns:

        alarmes = (
            df.groupby("equipement_id")["alarme_id"]
            .apply(lambda s: s.notna().sum())
            .rename("nb_alarmes")
            .reset_index()
        )

        equipements = equipements.merge(
            alarmes,
            on="equipement_id",
            how="left",
        )

    else:
        equipements["nb_alarmes"] = 0

    equipements["nb_alarmes"] = (
        equipements["nb_alarmes"]
        .fillna(0)
        .astype(int)
    )

    return equipements

# ============================================================
# 7. CHARGEMENT ET PREPARATION GENERALE
# ============================================================

try: 
    df = charger_donnees() 
    modele = charger_modele()

except Exception as erreur: 
    st.error(f"Erreur de chargement : {erreur}") 
    st.stop()

if df.empty: 
    st.warning( "Aucune donnée disponible dans fait_production." ) 
    st.stop()

if "equipement_id" not in df.columns: 
    st.error( "La colonne equipement_id est introuvable." ) 
    st.stop()

if modele is None: 
    st.warning( "Modèle non trouvé. Exécute le script d'entraînement " "pour créer data/modele_degradation.joblib." ) 
else: 
    st.success( f"Modèle chargé — horizon de prédiction : " f"{HORIZON_HEURES} heures." )

try: 
    df = calculer_predictions(df, modele) 
    equipements = construire_indicateurs(df)

except Exception as erreur: 
    st.error( f"Erreur pendant le calcul des prédictions : {erreur}" ) 
    st.stop()

derniere_date = df["timestamp"].max()

if pd.isna(derniere_date): 
    st.error( "Impossible de déterminer la date des dernières mesures." ) 
    st.stop()

# ============================================================
# 8. MENU ET FILTRES
# ============================================================

page = st.sidebar.radio( "Navigation", [ "Dashboard", "Équipements", "Fiche équipement", "Décisions", ], )

st.sidebar.divider() 
st.sidebar.subheader("Filtres")

sites = sorted( equipements["site"].dropna().astype(str).unique() )

types = sorted( equipements["type_equipement"] .dropna() .astype(str) .unique() )

sites_choisis = st.sidebar.multiselect( "Site", sites, default=sites, )

types_choisis = st.sidebar.multiselect( "Type d'équipement", types, default=types, )

seuil = st.sidebar.slider( "Seuil de risque (%)", min_value=0, max_value=100, value=70, step=5, ) / 100

st.sidebar.caption( "Un équipement est prioritaire si son score atteint " "ou dépasse le seuil." )

filtres = equipements[ equipements["site"].astype(str).isin(sites_choisis) & equipements["type_equipement"].astype(str).isin( types_choisis ) ].copy()

filtres["prioritaire"] = ( filtres["score_risque"] >= seuil ).fillna(False)

# ============================================================
# 9. PAGE DASHBOARD
# ============================================================

if page == "Dashboard":

    st.header("Vue d'ensemble")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Équipements suivis", filtres["equipement_id"].nunique(),)

    nb_prioritaires = int(filtres["prioritaire"].sum())

    c2.metric("Équipements à risque", nb_prioritaires if modele is not None else "N/D",)

    c3.metric("Alarmes associées", int(filtres["nb_alarmes"].sum()),)

    score_moyen = filtres["score_risque"].mean()

    c4.metric("Risque moyen", (f"{score_moyen:.1%}" if pd.notna(score_moyen) else "N/D"),)

    st.caption(
        f"Dernière mesure disponible : {derniere_date} "
        f"| Horizon : {HORIZON_HEURES} h "
        f"| Seuil opérationnel : {seuil:.0%}"
    )

    st.divider()

    gauche, droite = st.columns(2)

    with gauche:
        st.subheader("Puissance moyenne par équipement")

        fig = px.bar(
            filtres,
            x="equipement_id",
            y="puissance_moyenne",
            color="site",
            title="Puissance moyenne mesurée (W)",
        )

        st.plotly_chart(
            fig,
            use_container_width=True,
        )

    with droite:
        st.subheader("Distribution des scores de risque")

        scores_valides = filtres.dropna(
            subset=["score_risque"]
        )

        if not scores_valides.empty:

            fig = px.histogram(
                scores_valides,
                x="score_risque",
                nbins=20,
                title="Probabilités estimées de dégradation",
            )

            fig.add_vline(
                x=seuil,
                line_dash="dash",
                line_color="red",
                annotation_text=f"Seuil : {seuil:.0%}",
            )

            fig.update_xaxes(
                tickformat=".0%",
                range=[0, 1],
            )

            st.plotly_chart(
                fig,
                use_container_width=True,
            )

        else:
            st.info("Aucun score disponible.")

    st.subheader("Équipements prioritaires")

    prioritaires = (
        filtres[filtres["prioritaire"]]
        .sort_values("score_risque", ascending=False)
    )

    if modele is None:
        st.info(
            "Les prédictions sont indisponibles tant que "
            "le modèle n'a pas été sauvegardé."
        )

    elif prioritaires.empty:
        st.info(
            "Aucun équipement ne dépasse le seuil de risque."
        )

    else:
        colonnes = [
            "equipement_id",
            "site",
            "type_equipement",
            "score_risque",
            "niveau_risque",
            "derniere_performance",
            "nb_alarmes",
            "derniere_mesure",
        ]

        affichage = prioritaires[colonnes].copy()

        affichage["score_risque"] = (
            affichage["score_risque"].map(
                lambda x: f"{x:.1%}"
            )
        )

        st.dataframe(
            affichage,
            use_container_width=True,
            hide_index=True,
        )

# ============================================================
# 10. PAGE EQUIPEMENTS
# ============================================================

elif page == "Équipements":

    st.header("Équipements à surveiller")

    recherche = st.text_input("Rechercher un équipement")

    resultat = filtres.copy()

    if recherche:
        resultat = resultat[
            resultat["equipement_id"]
            .astype(str)
            .str.contains(
                recherche,
                case=False,
                na=False,
            )
        ]

    uniquement_prioritaires = st.checkbox("Afficher uniquement les équipements prioritaires")

    if uniquement_prioritaires:
        resultat = resultat[
            resultat["prioritaire"]
        ]

    resultat = resultat.sort_values(
        "score_risque",
        ascending=False,
        na_position="last",
    )

    colonnes = [
        "equipement_id",
        "site",
        "type_equipement",
        "puissance_moyenne",
        "derniere_performance",
        "score_risque",
        "niveau_risque",
        "nb_alarmes",
        "derniere_mesure",
    ]

    affichage = resultat[colonnes].copy()

    affichage["score_risque"] = (
        affichage["score_risque"].map(
            lambda x: f"{x:.1%}" if pd.notna(x) else "N/D"
        )
    )

    st.dataframe(
        affichage,
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "Télécharger les équipements en CSV",
        data=resultat.to_csv(index=False).encode("utf-8"),
        file_name="equipements.csv",
        mime="text/csv",
    )

# ============================================================
# 11. PAGE FICHE EQUIPEMENT
# ============================================================

elif page == "Fiche équipement":

    st.header("Fiche détaillée d'un équipement")

    ids = sorted(
        filtres["equipement_id"].dropna().tolist(),
        key=str,
    )

    if not ids:
        st.warning("Aucun équipement disponible.")

    else:
        choix = st.selectbox(
            "Sélectionner un équipement",
            ids,
            format_func=str,
        )

        fiche = (
            df[df["equipement_id"] == choix]
            .sort_values("timestamp")
            .copy()
        )

        info = equipements.loc[
            equipements["equipement_id"] == choix
        ].iloc[0]

        a, b, c, d = st.columns(4)

        a.metric(
            "Puissance moyenne",
            (
                f"{info['puissance_moyenne']:.2f} W"
                if pd.notna(info["puissance_moyenne"])
                else "N/D"
            ),
        )

        b.metric(
            "Dernière performance",
            (
                f"{info['derniere_performance']:.3f}"
                if pd.notna(info["derniere_performance"])
                else "N/D"
            ),
        )

        c.metric(
            "Risque de dégradation",
            (
                f"{info['score_risque']:.1%}"
                if pd.notna(info["score_risque"])
                else "N/D"
            ),
        )

        d.metric(
            "Niveau de risque",
            str(info["niveau_risque"]),
        )

        if pd.notna(info["score_risque"]):

            if info["score_risque"] >= seuil:
                st.warning(
                    "Le score dépasse le seuil de risque configuré."
                )
            else:
                st.info(
                    "Le score reste sous le seuil configuré."
                )

        st.caption(
            f"Dernière observation : {info['derniere_mesure']}"
        )

        st.subheader("Historique de puissance")

        fig = px.line(
            fiche,
            x="timestamp",
            y="puissance_w",
            title=f"Puissance mesurée — {choix}",
        )

        st.plotly_chart(
            fig,
            use_container_width=True,
        )

        if "performance" in fiche.columns:

            st.subheader("Historique de performance")

            fig = px.line(
                fiche,
                x="timestamp",
                y="performance",
                title="Évolution de la performance",
            )

            st.plotly_chart(
                fig,
                use_container_width=True,
            )

        if "severite_alarme" in fiche.columns:

            st.subheader("Historique des alarmes")

            alarmes_fiche = fiche[
                fiche["severite_alarme"].notna()
            ]

            if not alarmes_fiche.empty:

                st.dataframe(
                    alarmes_fiche[
                        [
                            "timestamp",
                            "severite_alarme",
                        ]
                    ].sort_values(
                        "timestamp",
                        ascending=False,
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

            else:
                st.info(
                    "Aucune alarme renseignée pour cet équipement."
                )

        st.subheader("Historique des mesures")

        colonnes = [
            c for c in [
                "timestamp",
                "puissance_w",
                "temperature_c",
                "rayonnement",
                "tension_v",
                "courant_a",
                "performance",
                "perte_production",
                "phase",
            ]
            if c in fiche.columns
        ]

        st.dataframe(
            fiche[colonnes].tail(200).sort_values(
                "timestamp",
                ascending=False,
            ),
            use_container_width=True,
            hide_index=True,
        )

# ============================================================
# 12. PAGE DECISIONS DE MAINTENANCE
# ============================================================

elif page == "Décisions":

    st.header("Aide à la décision")

    ids = sorted(
        filtres["equipement_id"].dropna().tolist(),
        key=str,
    )

    if not ids:
        st.warning("Aucun équipement disponible.")

    else:
        choix = st.selectbox(
            "Équipement concerné",
            ids,
            format_func=str,
        )

        info = equipements.loc[
            equipements["equipement_id"] == choix
        ].iloc[0]

        score = info["score_risque"]

        st.subheader("Évaluation du risque")

        gauche, droite = st.columns(2)

        gauche.metric(
            "Score de dégradation",
            (
                f"{score:.1%}"
                if pd.notna(score)
                else "N/D"
            ),
        )

        droite.metric(
            "Niveau de risque",
            str(info["niveau_risque"]),
        )

        if pd.notna(score):

            if score >= seuil:
                st.warning(
                    "Risque supérieur ou égal au seuil : "
                    "une inspection peut être envisagée."
                )
            else:
                st.info(
                    "Risque inférieur au seuil configuré."
                )

        else:
            st.info(
                "Aucune prédiction disponible pour cet équipement."
            )

        with st.form("form_decision"):

            decision = st.radio(
                "Décision",
                [
                    "Planifier une inspection",
                    "Ne rien faire pour le moment",
                ],
            )

            cout = st.number_input(
                "Coût estimé (€)",
                min_value=0.0,
                value=150.0,
                step=25.0,
            )

            commentaire = st.text_area(
                "Commentaire de l'opérateur"
            )

            valider = st.form_submit_button(
                "Enregistrer la décision"
            )

        if valider:

            nouvelle = pd.DataFrame([{
                "date_decision": pd.Timestamp.now().isoformat(),
                "equipement_id": choix,
                "decision": decision,
                "cout_estime_euros": cout,
                "score_risque": (
                    float(score) if pd.notna(score) else None
                ),
                "niveau_risque": str(info["niveau_risque"]),
                "seuil_risque": seuil,
                "commentaire": commentaire,
            }])

            try:
                if DECISIONS_PATH.exists():

                    ancien = pd.read_csv(
                        DECISIONS_PATH
                    )

                    historique = pd.concat(
                        [ancien, nouvelle],
                        ignore_index=True,
                    )

                else:
                    historique = nouvelle

                historique.to_csv(
                    DECISIONS_PATH,
                    index=False,
                )

                st.success(
                    "Décision enregistrée avec succès."
                )

            except Exception as erreur:
                st.error(
                    f"Impossible d'enregistrer la décision : {erreur}"
                )

    st.divider()
    st.subheader("Historique des décisions")

    if DECISIONS_PATH.exists():

        try:
            historique = pd.read_csv(
                DECISIONS_PATH
            )

            if not historique.empty:

                historique = historique.sort_values(
                    "date_decision",
                    ascending=False,
                )

                st.dataframe(
                    historique,
                    use_container_width=True,
                    hide_index=True,
                )

                st.download_button(
                    "Télécharger les décisions",
                    data=historique.to_csv(
                        index=False
                    ).encode("utf-8"),
                    file_name="decisions.csv",
                    mime="text/csv",
                )

            else:
                st.info(
                    "Aucune décision n'a encore été enregistrée."
                )

        except Exception as erreur:
            st.error(
                f"Erreur de lecture de l'historique : {erreur}"
            )

    else:
        st.info("Aucune décision n'a encore été enregistrée.")