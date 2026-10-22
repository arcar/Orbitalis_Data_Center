# Prédiction des dégradations de performance : démarche et résultats

Oct 22, 2026 · @Cedric

## Résumé

Une règle simple, qui déclenche une inspection quand la performance minimale au soleil des 9 dernières heures est inférieure ou égale à environ 0,48, réduit le coût de maintenance de 43 à 52 % par rapport à ne rien faire, et fait aussi bien qu'un Random Forest, voire mieux.

La question était d'anticiper, 9 h à l'avance, le passage de la performance d'un équipement sous 0,45 au soleil, afin de décider d'une inspection (150 €) plutôt que de subir une dégradation non anticipée (2 500 €). L'étude porte sur 73 épisodes de dégradation répartis sur 48 équipements.

Le Random Forest, la régression logistique et les tendances physiques (température, tension, courant, puissance) n'ont pas amélioré le résultat de la règle simple. La régression logistique est même significativement moins bonne sur des équipements jamais vus. La règle est donc la solution à retenir avec ces données, en attendant un historique plus long.

## Contexte et données

Les données viennent d'une base en étoile DuckDB de télémétrie d'équipements, avec un point toutes les 5 minutes (environ 631 000 lignes pour 48 équipements). Une orbite dure 90 minutes et alterne deux phases : ensoleillement et éclipse.

| Table | Contenu |
| --- | --- |
| fait\_production | Puissance, température, rayonnement, tension, courant, performance, ratio de vie, phase, et cinq clés de maintenance (corrective, calibration, inspection, préventive, remplacement) |
| dim\_equipement | Type, puissance nominale, rendement nominal, durée de vie, date d'installation, site |
| dim\_temps | Date et heure décomposées (identifiant AAAAMMJJHHMM) |
| dim\_alarme | Type et sévérité des alarmes |
| dim\_maintenance | Type et durée des interventions |

La performance (entre 0 et 1) tombe naturellement vers 0 pendant les éclipses. Seule la performance mesurée pendant la phase ensoleillée sert donc à définir une dégradation : Sur l'ensemble des données, 77 % des mesures sont sous 0,65, dont la plupart de celles des éclipses ; même au soleil, 65 % le sont, ce qui a conduit à retenir le seuil de 0,45 pour définir une dégradation.

## Définition du problème

Une dégradation est un épisode : la première mesure de performance sous 0,45 pendant l'ensoleillement, après au moins 9 h sans mesure sous ce seuil. La base contient 73 épisodes pour 48 équipements.

On ne prédit que pour les équipements sains : une ligne est éligible si aucune mesure n'est passée sous 0,45 depuis 9 h (287 260 lignes sur 622 032). La cible est qu'un nouvel épisode commence dans les 9 h qui suivent, ce qui donne 6 703 lignes positives, soit 2,3 %.

Le modèle produit un score par ligne. Pour chaque équipement, la première ligne dont le score dépasse le seuil de décision déclenche une inspection, puis l'équipement reste silencieux 9 h. L'alerte est juste si un épisode débute dans les 9 h, fausse sinon. Le coût est calculé par épisode et par alerte, avec le barème suivant.

| Situation | Coût |
| --- | --- |
| Dégradation non anticipée | 2 500 € |
| Inspection inutile | 150 € |
| Dégradation correctement anticipée | 300 € |
| Pas de dégradation, pas d'inspection | 0 € |

Avec ce barème, une alerte est rentable si elle est juste dans plus de 6,4 % des cas, soit 150 / (150 + 2 500 − 300).

## Variables construites

Toutes les variables n'utilisent que le passé de la ligne, pour ne pas laisser fuir la cible. Elles forment cinq familles.

- **Historique de performance au soleil** : valeurs décalées (de 1 à 108 pas), moyenne, écart-type et minimum sur 18, 54 et 108 pas (1, 3 et 6 orbites), tendance sur 6 orbites et distance au seuil de 0,45.
- **Signaux physiques** : température, tension, courant et puissance (moyenne sur une orbite, variation d'un pas), écart à la température ambiante.
- **Tendances physiques au soleil** : moyenne, écart-type et pente de la température, de la tension, du courant, de la puissance, du rayonnement et de l'écart de température, sur 3 et 6 orbites.
- **Alarmes et maintenance** : nombre d'alarmes sur 1 et 6 orbites ; pour chacun des cinq types de maintenance, nombre d'interventions sur 6 orbites et sur 1 jour, et temps écoulé depuis la dernière ; indicateur « en maintenance » et durée cumulée sur 1 jour.
- **Caractéristiques de l'équipement** : puissance nominale, rendement nominal, type d'équipement et ratio de vie.

## Protocole d'évaluation

Le seuil de décision est toujours choisi avant l'évaluation finale. Pour chaque pli, le modèle est entraîné sur le passé (avec un trou de 9 h pour que les cibles ne se recouvrent pas), le seuil qui minimise le coût est cherché sur une validation interne, puis appliqué une seule fois au test. Choisir le seuil sur le test donnerait une estimation trop optimiste.

Deux validations complémentaires sont réalisées.

1. **Blocs temporels** : la période est découpée en 8 segments de durée égale, et le test porte tour à tour sur les segments 4 à 8, avec un entraînement sur tout ce qui précède. Le seuil d'un pli s'appuie sur les courbes de coût de validation de ce pli et des plis précédents, additionnées puis lissées. 52 épisodes sont évaluables.
2. **Équipements inconnus** : 5 plis par groupes d'équipements, le test portant sur des équipements absents de l'entraînement. 73 épisodes sont évaluables.

L'incertitude des écarts de coût est mesurée par un bootstrap sur les équipements (3 000 tirages), qui tient compte du fait que les mesures d'un même équipement sont liées. Un modèle n'est jugé meilleur qu'un autre que si l'intervalle de confiance à 95 % de leur écart de coût ne contient pas 0.

## Modèles comparés

Les modèles d'apprentissage sont mis en face de règles très simples et de références de coût.

| Stratégie | Description |
| --- | --- |
| Jamais inspecter | Aucune alerte : toutes les dégradations sont subies (2 500 € chacune) |
| Inspection périodique | Une inspection toutes les 9 h pour chaque équipement sain |
| Baseline | Alerte si la performance minimale des 9 dernières heures est sous 1,15 × 0,45 = 0,5175, sans réglage |
| Règle perf\_min\_18, 54 ou 108 | Alerte quand la performance minimale sur la fenêtre passe sous un seuil réglé sur la validation |
| Règle réglée | Pour chaque pli, la fenêtre (18, 54 ou 108 pas) dont le coût de validation est le plus bas, avec son seuil |
| Régression logistique | 5 variables : perf\_min\_108, perf\_min\_18, perf\_mean\_54, perf\_std\_108, ratio\_vie |
| Random Forest | 200 arbres, profondeur 8, toutes les variables, classes pondérées |
| Oracle | Anticipation parfaite de chaque épisode (300 €), sans fausse alerte |

## Résultats

La règle réglée est la stratégie la moins chère dans les deux validations. Le Random Forest et la régression logistique coûtent plus cher qu'elle, de façon significative pour la logistique sur des équipements inconnus.

### Blocs temporels (52 épisodes)

| Stratégie | Coût total | Économie vs ne rien faire | Rappel | Précision | Fausses alertes | Anticipation médiane |
| --- | --- | --- | --- | --- | --- | --- |
| Jamais inspecter | 130 000 € | 0 % | 0 % | n/a | 0 | n/a |
| Inspection périodique | 205 800 € | −58 % | 100 % | 3,9 % | 1 268 | 4,0 h |
| Baseline (facteur 1,15) | 73 550 € | 43 % | 90 % | 13,1 % | 313 | 4,0 h |
| **Règle réglée** | **61 850 €** | **52 %** | 85 % | 18,7 % | 191 | 3,2 h |
| Régression logistique | 75 400 € | 42 % | 69 % | 18,0 % | 164 | 2,7 h |
| Random Forest | 77 850 € | 40 % | 54 % | 30,8 % | 63 | 3,0 h |
| Oracle | 15 600 € | 88 % | 100 % | 100 % | 0 | n/a |

### Équipements inconnus (73 épisodes)

| Stratégie | Coût total | Économie vs ne rien faire | Rappel | Précision | Fausses alertes | Anticipation médiane |
| --- | --- | --- | --- | --- | --- | --- |
| Jamais inspecter | 182 500 € | 0 % | 0 % | n/a | 0 | n/a |
| Inspection périodique | 414 450 € | −127 % | 100 % | 2,7 % | 2 617 | 3,9 h |
| Baseline (facteur 1,15) | 117 950 € | 35 % | 77 % | 12,5 % | 391 | 4,6 h |
| **Règle réglée** | **104 150 €** | **43 %** | 64 % | 22,0 % | 167 | 2,8 h |
| Régression logistique | 127 800 € | 30 % | 51 % | 17,2 % | 178 | 3,4 h |
| Random Forest | 110 850 € | 39 % | 59 % | 21,9 % | 153 | 3,1 h |
| Oracle | 21 900 € | 88 % | 100 % | 100 % | 0 | n/a |

### Écarts de coût et incertitude

Les valeurs sont des écarts de coût (A − B) avec leur intervalle de confiance à 95 % ; un nombre négatif signifie que A est moins cher.

| Écart A − B | Blocs temporels | Équipements inconnus |
| --- | --- | --- |
| Règle réglée − Jamais inspecter | −67 900 € \[−103 700 ; −35 600\] | −78 200 € \[−113 400 ; −45 700\] |
| Règle réglée − Baseline | −11 800 € \[−22 500 ; +450\] | −13 900 € \[−30 200 ; +2 400\] |
| Random Forest − Règle réglée | +16 200 € \[−9 000 ; +42 300\] | +6 800 € \[−7 000 ; +19 600\] |
| Régression logistique − Règle réglée | +13 500 € \[−950 ; +28 900\] | +23 600 € \[+4 000 ; +44 600\] |
| Régression logistique − Random Forest | −2 700 € \[−26 300 ; +20 600\] | +16 700 € \[−4 000 ; +41 500\] |

Sur le classement des lignes, indépendamment du seuil, la règle sur 108 pas a une ROC-AUC de 0,905 en blocs temporels et de 0,825 sur équipements inconnus, contre 0,852 et 0,856 pour le Random Forest. Le classement est donc comparable ; la différence de coût vient surtout du comportement d'alerte.

## Pièges rencontrés et corrections

Plusieurs résultats très flatteurs des premières versions se sont révélés trompeurs. Les corriger a changé la conclusion.

| Piège | Effet observé | Correction |
| --- | --- | --- |
| Cible définie sur toute la performance | 100 % des lignes positives, car chaque éclipse fait chuter la performance sous le seuil | Mesurer la performance uniquement pendant l'ensoleillement |
| Prédire sur tous les équipements, y compris déjà dégradés | ROC-AUC de 0,993 : le modèle reconnaissait surtout un état dégradé en cours, pas l'arrivée d'une dégradation | Ne garder que les lignes saines depuis 9 h et viser un nouvel épisode ; la ROC-AUC tombe autour de 0,85 |
| Coût compté par ligne de 5 minutes | Une même dégradation était comptée des dizaines de fois : 85 % d'économie annoncée | Coût par épisode et par alerte avec 9 h de silence après chaque alerte |
| Un seul découpage de test (21 à 23 épisodes) | Le Random Forest semblait 30 % moins cher que la baseline | Validation en 5 blocs temporels et par équipement, avec intervalles de confiance ; l'avantage disparaît |
| Épisodes d'équipements hors test comptés comme manqués | Coûts et rappels faux dans la validation par équipement | Ne compter que les épisodes des équipements testés ; les écarts entre stratégies n'étaient pas affectés |

## Limites

Les conclusions valent pour cet historique et ce barème, et sont limitées par la taille des données.

- **Peu d'épisodes** : 73 sur 48 équipements, observés sur environ deux mois (mai et juin 2025). Les intervalles de confiance sont larges, et l'avantage de la règle sur la baseline frise le zéro.
- **Base régénérée** : le nombre d'épisodes est passé de 107 à 73 quand le schéma de maintenance a changé, ce qui indique que les données ont été recréées. Les résultats ne sont pas comparables d'une version de la base à l'autre.
- **Maintenance peu exploitable** : le nombre de lignes de maintenance de chaque type est un multiple exact de 48 (le nombre d'équipements), ce qui suggère un calendrier planifié plutôt que des interventions déclenchées par des dégradations ; cette hypothèse n'a pas été vérifiée. Ces variables pèsent moins de 5 % de l'importance du Random Forest.
- **Barème supposé fixe** : les coûts sont les mêmes pour tous les équipements, et l'effet d'une inspection sur l'état de l'équipement n'est pas modélisé (simple silence de 9 h).
- **Seuil de dégradation de 0,45** : choisi pour obtenir une classe positive exploitable. Un autre seuil métier demanderait de refaire l'étude.
- **Préavis court** : l'anticipation médiane de la règle réglée est de 2,8 à 3,2 h, ce qui peut être insuffisant pour planifier une intervention.

## Recommandations et suite

Retenir la règle simple plutôt qu'un modèle d'apprentissage : elle coûte moins cher, se règle sur la validation et s'explique en une phrase. Les seuils choisis par pli sont stables (score de 0,51 à 0,53, soit une performance minimale sur 9 h inférieure ou égale à environ 0,47-0,49). Elle économise environ 1 100 à 1 300 € par épisode, soit la moitié à 60 % du gain maximal de 2 200 €.

1. **Valider avec l'équipe de maintenance** : la règle déclenche 3 à 4 fausses alertes pour une alerte juste, ce qui demande une capacité d'inspection correspondante. Vérifier aussi que le seuil de 0,45 et le préavis de 3 h conviennent.
2. **Arbitrer préavis et coût** : un seuil plus haut, comme la baseline, donne 4 à 4,6 h d'anticipation mais coûte 12 000 à 14 000 € de plus sur ces données.
3. **Allonger l'historique** : plusieurs mois, voire plusieurs saisons, puis rejouer la validation en blocs. Le Random Forest s'améliorait quand l'entraînement grossissait (blocs 3 à 5), il mérite d'être réévalué avec plus de données.
4. **Clarifier la maintenance** : savoir si elle est planifiée ou déclenchée. Des interventions déclenchées par des dégradations pourraient devenir un vrai signal.
5. **Chercher des signaux nouveaux** : les tendances de température, tension, courant et puissance n'ont rien ajouté à l'historique de performance ; il faut des mesures qui précèdent réellement la chute pour que l'apprentissage fasse mieux que la règle.
