SV_PATH = "data/raw/energy_production_dataset.csv"
OUTPUT_PATH = "data/energy_production_dataset_propre.csv"
SQL_DB = "data/energie.db"



def lire_csv():
    df = pd.read_csv(CSV_PATH)
    return df

def nettoyage_csv(df):

    print("1 : Vérification dtypes et non-null")
    print("-" * 40)
    df.info()

    print("2 : Vérification si valeurs manquantes")
    print("-" * 40)
    print(df.isnull().sum())
    valeurs_manquantes = df.isnull().sum().sum()
    print(f"\nTotal valeurs manquantes : {valeurs_manquantes}\n")

    print("3 : Vérification et suppression doublons")
    print("-" * 40)
    nb_lignes_avant = len(df)
    print(f"\nNombre de données : {nb_lignes_avant}")
    doublons_count = df.duplicated().sum()
    print(f"\nNombre de doublons : {doublons_count}")
    if doublons_count > 0:
        df = df.drop_duplicates()
        nb_lignes_apres = len(df)
        print(f"\nDoublons supprimés, nouveau nombre de données : {nb_lignes_apres}\n")
    else : 
        nb_lignes_apres = len(df)


    print("4 : Modification format date")
    print("-" * 40)
    df["Date"] = pd.to_datetime(df["Date"], format="mixed", errors="coerce")


    print("5 : Ne conserver que les données Solaire")
    print("-" * 40)
    df = df[~df["Source"].str.contains("Wind", case=False, na=False)]

    print("5 : Supprimer colonne Source")
    print("-" * 40)
    df = df.drop("Source", axis=1)


    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nFichier nettoyé : '{OUTPUT_PATH}'")

    return df


def creer_db (chemin_db = SQL_DB):
    connexion = sqlite3.connect(chemin_db)
    connexion.execute("""
        CREATE TABLE IF NOT EXISTS solaire (
            id_solar              INTEGER PRIMARY KEY AUTOINCREMENT,
            date                  DATE,
            start_hour            INTEGER,
            end_hour              INTEGER,
            day_of_year           INTEGER,
            day_name              TEXT,
            month_name            TEXT,
            season                TEXT,
            production            INTEGER
        )
    """)
    connexion.commit()
    connexion.close()


def alimenter_solaire(df, chemin_db=SQL_DB):
    
    connexion = sqlite3.connect(chemin_db)

    df_a_inserer = df.copy()
    df_a_inserer["Date"] = df_a_inserer["Date"].dt.strftime("%Y-%m-%d")
    df_a_inserer.to_sql("solaire", connexion, if_exists="replace", index=False)

    connexion.close()
    print(f"  {len(df)} lignes insérées dans db_solaire")

def executer_SQL(chemin_db=SQL_DB):
    connexion = sqlite3.connect(chemin_db)

    requetes = {
        "Nombre de relevés par saison": """
            SELECT season, COUNT(*) AS nb_releves
            FROM solaire GROUP BY season ORDER BY nb_releves DESC
        """,
        "Production moyenne par saison": """
            SELECT season, ROUND(AVG(production), 2) AS production_moyenne
            FROM solaire GROUP BY season ORDER BY production_moyenne DESC
        """,
        "Production moyenne par heure": """
            SELECT start_hour, end_hour, ROUND(AVG(production), 2) AS production_moyenne
            FROM solaire GROUP BY start_hour ORDER BY start_hour
        """,
        "Top 10 jours de production": """
            SELECT date, production FROM solaire
            ORDER BY production DESC LIMIT 10
        """,
        "Production moyenne par jour de semaine": """
            SELECT day_name, ROUND(AVG(production), 2) AS production_moyenne
            FROM solaire GROUP BY day_name ORDER BY production_moyenne DESC
        """,
    }

    for titre, requete in requetes.items():
        print(f"\n{titre}")
        print("-" * 40)
        resultat = pd.read_sql(requete, connexion)
        print(resultat.to_string(index=False))

    connexion.close()
