# 1. Prise en main et exploration
## Quelles sont les sources disponibles et leur grain ?
    - sites.csv : chaque ligne concerne une station, son emplacement, son inclinaison
    - equipements.csv : chaque ligne concerne un equipement et son lieu d'installation ainsi que le modèle
    - maintenance.csv : chaque ligne concerne une intervention et son type
    - orbite.csv : chaque ligne correspond aux données de rayonnement et temperature par pas de 15 min pour chaque site
    - telemetrie.csv : chaque ligne correspond aux données télémétriques de chaque equipement par pas de 5 min
    - catalogue.db : table modeles : chaque ligne correspond aux caracteristique d'un modele d'equipement
                    reference site : chaque ligne correspond a un site
                    seuil alarme : chaque ligne correspond a un type d'alarme et a son seuil
    - alarmes.json : chaque ligne correspond a un declenchement d'alarme

## Quelles données semblent fiables ou problématiques ?
    - sites.csv : manque altitude ou inclinaison pour 2 sites
    - equipements.csv : doublon?, un equipement incohérent, format date, site?
    - maintenance.csv : données manquantes, mauvais nom de colonne, cout negatif
    - orbite.csv : pas d'erreurs apparentes
    - telemetrie.csv : format date, coherence des données, equipements bien presents?
    - catalogue.db : modeles : RAS 'données radiateur?'
                    reference site : pb description et capacité
                    seuil alarme : RAS
    - alarmes.json : apparemment RAS, a controler!


## Quelles relations peut-on établir entre les sources ?
Il y a un lien entre le fichier site.csv et la table references_sites dans catalogue.db. Tout deux reprennent les mêmes données et se complètent lorsque les valeurs sont manquantes.

Il y a des liens entre : 
- site.csv -> equipements.csv
- site.csv -> orbite.csv
- equipements.csv -> telemetrie.csv
- equipements.csv -> maintenance.csv
- equipements.csv -> catalogue.db (table : modeles)
- catalogue.db (table : seuils_alarmes) -> alarmes.json

## Quelles informations sont nécessaires pour analyser une dégradation de performance ?
On a besoin des informations suivantes :
- La puissance nominale présente dans equipement.csv par rapport aux données réelles fournies par la télémétrie.
- Le statut de chaque équipement
- La température à laquelle est soumis l'équipement
- Le rayonnement auquel est soumis l'équipement
- La durée de vie d'un équipement par rapport à sa date d'installation

## Points à vérifier avant toute transformation : 
- Le formats de dates
- Les valeurs manquantes
- Les valeurs aberrantes :
    * La date de début de maintenance est-elle antérieure à la date de fin ?
    * Y a t'il des valeurs négatives dans les données ?
    * La puissance nominale d'un équipement est inférieur à la puissance relevée lors de la télémétrie ?
- Les doublons
- clés étrangères invalides
- La cohérence des phases orbitales (ensoleillement / éclipse) et de leurs données

