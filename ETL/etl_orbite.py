import pandas as pd

CSV_PATH = "data/raw/orbite.csv"
OUTPUT_PATH = "data/orbite_propre.csv"



def lire_csv():
    df = pd.read_csv(CSV_PATH)
    return df

def nettoyage_csv(df):

    print("\n1 : Vérification dtypes et non-null")
    print("-" * 40)
    df.info()

    print("\n2 : Vérification si valeurs manquantes")
    print("-" * 40)
    print(df.isnull().sum())
    valeurs_manquantes = df.isnull().sum().sum()
    print(f"\nTotal valeurs manquantes : {valeurs_manquantes}\n")

    print("\n3 : Vérification et suppression doublons")
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


    print("\n4 : Modification format date")
    print("-" * 40)
    df["Date"] = pd.to_datetime(df["timestamp"], format="ISO 8601", errors="coerce")


    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nFichier nettoyé : '{OUTPUT_PATH}'")

    return df


def main():
    df = lire_csv()
    df = nettoyage_csv(df)


main()