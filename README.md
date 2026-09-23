# ORBITALIS — Data Engineering & Maintenance Prédictive
## Présentation du projet
ORBITALIS est un projet de traitement, d'analyse et de valorisation de données appliqué à un datacenter placé en orbite basse (LEO).

L'alimentation électrique du datacenter repose sur des panneaux solaires ainsi que sur plusieurs équipements de conversion et de stockage. Ces équipements sont soumis à des conditions particulières : alternance entre périodes d'ensoleillement et d'éclipse, variations importantes de température, rayonnement, vieillissement et risques de défaillance des capteurs ou convertisseurs.

L'objectif est de construire une chaîne Data robuste, reproductible et exploitable permettant de fiabiliser les données, analyser les performances des équipements et anticiper les dégradations avant qu'elles n'aient un impact significatif sur la production.

## Objectifs
Le projet couvre l'ensemble de la chaîne de traitement de la donnée :
- Explorer et comprendre les différentes sources de données.
- Identifier les problèmes de qualité et les incohérences.
- Mettre en place une chaîne ETL reproductible.
- Séparer les données en zones raw, cleaned et analytics.
- Harmoniser les formats, types et timestamps.
- Gérer les valeurs manquantes, aberrantes et les doublons.
- Contrôler l'intégrité référentielle entre les différentes sources.
- Conserver les données rejetées et les raisons des rejets.
- Construire un modèle décisionnel en schéma en étoile.
- Analyser la production, les pertes, les alarmes et les performances.
- Prendre en compte le contexte orbital dans l'analyse.
- Identifier les équipements présentant des signes de dégradation.
- Construire un modèle de maintenance prédictive.
- Optimiser le modèle selon un coût métier des erreurs de prédiction.

## Problématique
La problématique principale du projet est la suivante :
```Comment exploiter des données hétérogènes et imparfaites afin de surveiller la performance des équipements d'un datacenter orbital et d'anticiper les dégradations suffisamment tôt pour permettre une intervention de maintenance ?```


## Sources de données
Le projet utilise plusieurs sources provenant de systèmes différents :

| Source	| Format | Description |
|---|---|---|
| sites.csv | CSV | Référentiel des sites |
| equipements.csv | CSV | Référentiel des équipements |
| telemetrie.csv |CSV |	Mesures de télémétrie |
| alarmes.json |JSON| Alarmes générées par les équipements |
| maintenance.csv |CSV|	Historique des opérations de maintenance |
| orbite.csv | CSV | Phases orbitales et contexte d'ensoleillement |
| catalogue.db | SQLite | Données de référence complémentaires |

## Architecture du pipeline
Le traitement des données repose sur une architecture en trois couches :



Cette architecture permet de conserver les données originales tout en assurant la traçabilité des transformations.

## Qualité des données
Les valeurs manquantes et aberrantes ont été traitées de la manière suivante : 
### Données en doublon
Les doublons ont été retirés dans tous les fichiers sources.

### Données alarmes.json
#### Données manquantes
Les données manquantes concernant l'ID des équipements ont été remplies par l'ID présent dans le message d'alarme lorsqu'il était présent. Si après traitement l'ID est toujours manquant la ligne est rejetée.
#### Données aberrantes
Les types d'alarmes ont été vérifiée. Si un type après traitement est identifié comme UNKNOWN_TYPE celui-ci est remplacé par la valeur présente dans le message d'alarme. Si malgré cette vérification le type est toujours inconnu, la ligne est rejeté.

### Données orbite.csv
#### Données manquantes
Les données manquantes de la température ont été remplacées par la médiane du cycle en cours (ensoleillement, eclipse)
#### Données aberrantes
Les données de température ne répondant pas aux règles suivantes ont été rejetées :
    -> ```(df["phase"] == "ensoleillement") & (df["temperature_ambiante_c"] < -10) | (df["phase"] == "eclipse") & (df["temperature_ambiante_c"] >= -10)```

Les données de rayonnement ne répondant pas aux règles suivantes ont également été rejetées :
    -> ```(df["phase"] == "ensoleillement") & (df["rayonnement_solaire_w_m2"] > 0)) | ((df["phase"] == "eclipse") & (df["rayonnement_solaire_w_m2"] == 0)```

Les données de site n'étant pas présents dans le fichier sites.csv ont également été rejetées.

### Lignes rejetées
Toutes les lignes rejetées sont conservées dans une zone dédiée avec la raison du rejet, afin de garantir la traçabilité du pipeline.


## Modèle de données


## Analyses SQL


## Maintenance prédictive


## Technologies utilisées
Le projet s'appuie notamment sur :
- Python pour l'ingestion, le nettoyage et la modélisation
- Pandas / NumPy pour la manipulation des données
- SQLite pour le stockage et les analyses
- Scikit-learn pour la maintenance prédictive
- Matplotlib / Seaborn pour la visualisation ;

# Reproductibilité
L'ensemble du pipeline est conçu pour être reproductible.

Les transformations sont documentées et séparées des données sources. Les paramètres importants, les règles de qualité, les critères de rejet et les définitions des indicateurs sont explicités afin qu'une nouvelle exécution du pipeline puisse produire les mêmes résultats à partir des mêmes données d'entrée.
