from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st
import plotly.express as px

# ==================================================
# CONFIGURATION
# ==================================================

st.set_page_config(
    page_title="Orbitalis Data Center",
    layout="wide"
)

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "base_etoile.duckdb"

st.title("Orbitalis Data Center")
st.caption("Pilotage intelligent de la maintenance photovoltaïque")


# ==================================================
# CHARGEMENT DES DONNEES
# ==================================================

@st.cache_data(ttl=60)
def charger_donnees():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Base introuvable : {DB_PATH}"
        )

    con = duckdb.connect(str(DB_PATH), read_only=True)

    try:
        df = con.execute("""
            SELECT
                f.*,
                t.date AS date_mesure,
                t.heure,
                t.minute,
                e.site,
                e.type_equipement,
                e.puissance_nominale_w,
                e.date_installation
            FROM fait_production f
            LEFT JOIN dim_temps t
                ON f.temps_id = t.temps_id
            LEFT JOIN dim_equipement e
                ON f.equipement_id = e.equipement_id
        """).fetchdf()
    finally:
        con.close()

    df["date_mesure"] = pd.to_datetime(
        df["date_mesure"], errors="coerce"
    )

    return df


try:
    df = charger_donnees()
except Exception as erreur:
    st.error(f"Erreur de chargement : {erreur}")
    st.stop()

if df.empty:
    st.warning("Aucune donnée dans la table fait_production.")
    st.stop()

if "equipement_id" not in df.columns:
    st.error("La colonne equipement_id est introuvable.")
    st.stop()

# Dernière date réellement présente dans les données
derniere_date = df["date_mesure"].max()

if pd.isna(derniere_date):
    st.error("Impossible de déterminer la date des mesures.")
    st.stop()


# ==================================================
# CALCUL DES INDICATEURS PAR EQUIPEMENT
# ==================================================

equipements = (
    df.sort_values("temps_id")
    .groupby("equipement_id")
    .agg(
        site=("site", "first"),
        type_equipement=("type_equipement", "first"),
        puissance_moyenne=("puissance_w", "mean"),
        derniere_performance=("performance", "last"),
        nb_mesures=("temps_id", "count")
    )
    .reset_index()
)

# Date de la dernière mesure de chaque équipement
dernieres_dates = (
    df.dropna(subset=["date_mesure"])
    .groupby("equipement_id")["date_mesure"]
    .max()
    .rename("derniere_mesure")
    .reset_index()
)

equipements = equipements.merge(
    dernieres_dates,
    on="equipement_id",
    how="left"
)

# Nombre de mesures avec une alarme associée
if "alarme_id" in df.columns:
    alarmes = (
        df.groupby("equipement_id")["alarme_id"]
        .apply(lambda s: s.notna().sum())
        .rename("nb_alarmes")
        .reset_index()
    )

    equipements = equipements.merge(alarmes, on="equipement_id", how="left")
else:
    equipements["nb_alarmes"] = 0

equipements["nb_alarmes"] = (equipements["nb_alarmes"].fillna(0).astype(int))

# Score non renseigné tant que le modèle n'est pas intégré
equipements["score_risque"] = float("nan")


# ==================================================
# MENU ET FILTRES
# ==================================================

page = st.sidebar.radio(
    "Navigation",
    [
        "Dashboard",
        "Équipements",
        "Fiche équipement",
        "Décisions"
    ]
)

st.sidebar.divider()
st.sidebar.subheader("Filtres")

sites = sorted(
    equipements["site"].dropna().astype(str).unique()
)

types = sorted(
    equipements["type_equipement"]
    .dropna().astype(str).unique()
)

sites_choisis = st.sidebar.multiselect(
    "Site",
    sites,
    default=sites
)

types_choisis = st.sidebar.multiselect(
    "Type d'équipement",
    types,
    default=types
)

seuil = st.sidebar.slider(
    "Seuil de risque (%)",
    min_value=0,
    max_value=100,
    value=70,
    step=5
) / 100

st.sidebar.caption(
    "Le seuil sera utilisé lorsque le modèle sera intégré."
)

filtres = equipements[
    equipements["site"].astype(str).isin(sites_choisis)
    & equipements["type_equipement"].astype(str).isin(
        types_choisis
    )
].copy()


# ==================================================
# PAGE 1 : DASHBOARD
# ==================================================

if page == "Dashboard":

    st.header("Vue d'ensemble")

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "Équipements suivis",
        filtres["equipement_id"].nunique()
    )

    c2.metric(
        "Équipements à risque",
        "N/D"
    )

    c3.metric(
        "Alarmes associées",
        int(filtres["nb_alarmes"].sum())
    )

    st.info(
        "Le modèle de classification n'est pas encore connecté. "
        "Le nombre d'équipements à risque sera affiché "
        "après son intégration."
    )

    st.subheader("Puissance moyenne par équipement")

    fig = px.bar(
        filtres,
        x="equipement_id",
        y="puissance_moyenne",
        color="site",
        title="Puissance moyenne mesurée (W)"
    )

    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Dernières performances")

    st.dataframe(
        filtres[
            [
                "equipement_id",
                "site",
                "type_equipement",
                "derniere_performance",
                "nb_alarmes",
                "derniere_mesure"
            ]
        ].sort_values(
            "derniere_mesure",
            ascending=False
        ),
        use_container_width=True,
        hide_index=True
    )


# ==================================================
# PAGE 2 : LISTE DES EQUIPEMENTS
# ==================================================

elif page == "Équipements":

    st.header("Équipements à surveiller")

    recherche = st.text_input(
        "Rechercher un équipement"
    )

    resultat = filtres.copy()

    if recherche:
        resultat = resultat[
            resultat["equipement_id"]
            .astype(str)
            .str.contains(recherche, case=False, na=False)
        ]

    st.dataframe(
        resultat[
            [
                "equipement_id",
                "site",
                "type_equipement",
                "puissance_moyenne",
                "derniere_performance",
                "nb_alarmes",
                "derniere_mesure"
            ]
        ].sort_values(
            "puissance_moyenne",
            ascending=True,
            na_position="last"
        ),
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "Télécharger les équipements en CSV",
        data=resultat.to_csv(index=False).encode("utf-8"),
        file_name="equipements.csv",
        mime="text/csv"
    )


# ==================================================
# PAGE 3 : FICHE EQUIPEMENT
# ==================================================

elif page == "Fiche équipement":

    st.header("Fiche détaillée d'un équipement")

    ids = sorted(
        filtres["equipement_id"].dropna().unique().tolist()
    )

    if not ids:
        st.warning("Aucun équipement disponible.")
    else:
        choix = st.selectbox(
            "Sélectionner un équipement",
            ids
        )

        fiche = df[
            df["equipement_id"] == choix
        ].sort_values("temps_id")

        info = equipements[
            equipements["equipement_id"] == choix
        ].iloc[0]

        a, b, c = st.columns(3)

        a.metric(
            "Puissance moyenne",
            (
                f"{info['puissance_moyenne']:.2f} W"
                if pd.notna(info["puissance_moyenne"])
                else "N/D"
            )
        )

        b.metric(
            "Dernière performance",
            (
                f"{info['derniere_performance']:.3f}"
                if pd.notna(info["derniere_performance"])
                else "N/D"
            )
        )

        c.metric(
            "Alarmes associées",
            int(info["nb_alarmes"])
        )

        st.subheader("Historique de puissance")

        fig = px.line(
            fiche,
            x="date_mesure",
            y="puissance_w",
            title=f"Puissance mesurée — {choix}"
        )

        st.plotly_chart(fig, use_container_width=True)

        if "performance" in fiche.columns:
            st.subheader("Historique de performance")

            fig = px.line(
                fiche,
                x="date_mesure",
                y="performance",
                title="Évolution de la performance"
            )

            st.plotly_chart(fig, use_container_width=True)

        st.subheader("Historique des mesures")

        colonnes = [
            c for c in [
                "date_mesure",
                "puissance_w",
                "temperature_c",
                "rayonnement",
                "tension_v",
                "courant_a",
                "performance",
                "perte_production",
                "phase"
            ]
            if c in fiche.columns
        ]

        st.dataframe(
            fiche[colonnes].tail(200),
            use_container_width=True,
            hide_index=True
        )


# ==================================================
# PAGE 4 : DECISIONS DE MAINTENANCE
# ==================================================

elif page == "Décisions":

    st.header("Aide à la décision")

    ids = sorted(
        filtres["equipement_id"].dropna().unique().tolist()
    )

    if not ids:
        st.warning("Aucun équipement disponible.")
    else:
        choix = st.selectbox(
            "Équipement concerné",
            ids
        )

        with st.form("form_decision"):

            decision = st.radio(
                "Décision",
                [
                    "Planifier une inspection",
                    "Ne rien faire pour le moment"
                ]
            )

            cout = st.number_input(
                "Coût estimé (€)",
                min_value=0.0,
                value=150.0,
                step=25.0
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
                "commentaire": commentaire
            }])

            if DECISIONS_PATH.exists():
                ancien = pd.read_csv(DECISIONS_PATH)

                historique = pd.concat(
                    [ancien, nouvelle],
                    ignore_index=True
                )
            else:
                historique = nouvelle

            historique.to_csv(
                DECISIONS_PATH,
                index=False
            )

            st.success("Décision enregistrée avec succès.")

    st.subheader("Historique des décisions")

    if DECISIONS_PATH.exists():

        historique = pd.read_csv(DECISIONS_PATH)

        st.dataframe(
            historique.sort_values(
                "date_decision",
                ascending=False
            ),
            use_container_width=True,
            hide_index=True
        )

        st.download_button(
            "Télécharger les décisions",
            data=historique.to_csv(index=False).encode("utf-8"),
            file_name="decisions.csv",
            mime="text/csv"
        )

    else:
        st.info(
            "Aucune décision n'a encore été enregistrée."
        )