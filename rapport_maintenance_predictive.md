# Orbitalis – Maintenance prédictive

*Anticiper l'entrée en dégradation d'un équipement dans les 9 heures suivantes*

---

## 1. Contexte et objectif

Le projet Orbitalis suit des équipements d'une installation solaire en orbite (panneaux solaires, convertisseurs DC, batteries). La télémétrie est enregistrée toutes les 5 minutes, les mesures d'orbite (phase, rayonnement solaire, température ambiante) toutes les 15 minutes, et les interventions de maintenance par jour. Ce rapport traite la question de la maintenance prédictive : à une date de référence *t*, estimer si un équipement risque d'entrer dans un état dégradé au cours des prochaines 6 périodes orbitales, soit 9 heures.

L'objectif est de décider quels équipements inspecter, en tenant compte du coût réel des erreurs plutôt que d'une simple précision statistique.

## 2. Données et préparation

### 2.1 Entrepôt en étoile

Les données sources (base SQLite) ont été chargées dans une base DuckDB organisée en étoile. La table de faits `fait_production` contient une ligne par équipement et par pas de 5 minutes, avec la puissance, la température, la tension, le courant, le rayonnement, une performance, un ratio de vie de l'équipement, la phase orbitale, le rayonnement solaire et la température ambiante. Elle référence quatre dimensions : `dim_temps`, `dim_equipement`, `dim_alarme` et `dim_maintenance`.

Trois jointures demandaient une attention particulière. Les mesures d'orbite (pas de 15 minutes) sont rattachées à la télémétrie (pas de 5 minutes) par une jointure « dernière valeur connue » (`ASOF JOIN`). Les alarmes, qui sont des événements ponctuels, sont rangées dans la tranche de 5 minutes correspondante, en conservant la plus grave si plusieurs tombent dans la même tranche. La maintenance, définie par jour, est rattachée à chaque mesure du jour concerné, avec une seule intervention retenue par équipement et par jour en cas de chevauchement.

### 2.2 Anomalies corrigées avant la modélisation

Plusieurs défauts de qualité ont été identifiés et traités avant d'entraîner un modèle :

- **Dates inversées.** Les horodatages au format ISO étaient lus avec l'option « jour d'abord », ce qui inversait jour et mois (le 5 mai devenait le 5 janvier) pour les jours 1 à 12. Le nettoyage n'applique désormais cette option qu'aux formats non ISO. La période réelle va du 1er mai au 14 juin 2025.
- **Doublons de télémétrie.** Certaines lignes étaient présentes deux fois pour un même couple (équipement, horodatage), avec des valeurs légèrement différentes. Une seule ligne par couple est conservée.
- **Performances aberrantes.** Des valeurs de performance atteignant plusieurs centaines (rapport entre puissance mesurée et puissance nominale) sont physiquement impossibles. Les 284 valeurs supérieures à 1,5 ont été considérées comme invalides.
- **Batteries.** Leur performance hors éclipse a pour médiane 0,33 : le rapport puissance mesurée sur puissance nominale ne mesure pas leur état de santé, puisqu'une batterie se charge et se décharge. Le critère de performance n'est donc appliqué qu'aux panneaux solaires et aux convertisseurs DC. Les batteries restent dans l'étude via le critère des alarmes.

## 3. Définition de la variable cible

### 3.1 Définition retenue

Les dates de référence sont les heures pile de chaque équipement. Pour chacune, la cible vaut 1 si, dans la fenêtre des 9 heures suivantes (108 pas de 5 minutes, la date *t* exclue), au moins l'une des deux conditions suivantes est vérifiée :

1. la performance moyenne hors éclipse tombe en dessous de 0,65, la moyenne n'étant évaluée que si la fenêtre contient au moins une heure de mesures hors éclipse ;
2. l'équipement génère au moins une alarme de sévérité `critical` (ou `URGENCE`) de type `perte_puissance` ou `surchauffe`.

La performance est définie comme le rapport entre la puissance mesurée et la puissance nominale de l'équipement. Une date n'est retenue que si la fenêtre future de 9 heures est entièrement disponible dans les données.

### 3.2 Prédire l'entrée en dégradation

Une première version, qui incluait tous les équipements, produisait entre 54 % et 86 % de cas positifs : la cible était presque toujours vraie, car un équipement déjà dégradé le reste. Le modèle apprenait cette persistance et battait à peine la politique triviale « toujours inspecter » (2 % d'économie). Le problème à résoudre étant l'*entrée* en dégradation, seules les dates où l'équipement est sain à l'instant *t* sont conservées : performance hors éclipse des 3 dernières heures supérieure ou égale à 0,65 (ou non définie), et aucune alarme critique de puissance ou de température dans les 3 dernières heures.

Cette restriction retient 30 440 dates de référence sur 52 176 (58 %). Les cas positifs représentent alors 2,1 % de l'ensemble d'entraînement, soit un événement rare, ce qui correspond à la réalité d'une maintenance prédictive.

| Type d'équipement | Dates retenues | % de positifs |
|---|---|---|
| Batterie | 12 912 | 0,9 % |
| Convertisseur DC | 8 719 | 2,6 % |
| Panneau solaire | 8 809 | 4,0 % |

## 4. Variables explicatives

Toutes les variables explicatives n'utilisent que le passé et le présent de la date *t* ; aucune fenêtre ne regarde vers l'avant, contrairement à la cible. Elles comprennent les valeurs instantanées (puissance, température, tension, courant, rayonnement, rayonnement solaire, température ambiante, performance), des moyennes et écarts-types glissants sur 1 h et 3 h ainsi que la variation sur 1 h pour la puissance, la température et la performance, la performance moyenne hors éclipse des 3 dernières heures, le nombre d'alarmes critiques des 3 dernières heures, un indicateur d'éclipse, un indicateur de maintenance en cours, l'heure sous forme cyclique, le type d'équipement, sa puissance nominale et son ratio de vie. Les valeurs manquantes sont remplacées par la médiane de l'entraînement pour la régression logistique ; le Gradient Boosting les gère nativement.

## 5. Protocole de modélisation

**Découpage chronologique.** Les observations ne sont jamais mélangées. Les dates sont réparties selon le temps : les 60 % les plus anciennes pour l'entraînement, les 20 % suivants pour la validation, les 20 % finaux pour le test. Comme la cible de chaque ligne regarde 9 heures devant elle, un embargo de 9 heures est observé entre les jeux : les dates situées dans cet intervalle (324 au total) sont écartées, ce qui évite que la fin d'un jeu « connaisse » le début du suivant.

| Jeu | Période | Dates de référence | % positifs |
|---|---|---|---|
| Entraînement | 1er mai 00:00 → 27 mai 18:00 | 22 161 | 2,1 % |
| Validation | 28 mai 03:00 → 5 juin 16:00 | 4 144 | 1,9 % |
| Test | 6 juin 01:00 → 14 juin 14:00 | 3 811 | 3,1 % |

**Modèles.** Deux modèles sont comparés : une régression logistique (avec imputation par la médiane et standardisation apprises sur l'entraînement) et un Gradient Boosting (arbres de profondeur 4, 200 itérations, taux d'apprentissage 0,05). L'arrêt anticipé du Gradient Boosting est désactivé, car il tirerait aléatoirement un sous-ensemble de validation et romprait l'ordre chronologique. Aucune pondération de classes n'est utilisée, pour que les probabilités restent exploitables dans le calcul de coût.

## 6. Coût métier et choix du seuil

### 6.1 Matrice de coûts

| Situation | Coût |
|---|---|
| Dégradation non anticipée (faux négatif) | 2 500 € |
| Inspection inutile (faux positif) | 150 € |
| Dégradation correctement anticipée (vrai positif) | 300 € |
| Pas de dégradation, pas d'inspection (vrai négatif) | 0 € |

Pour une probabilité bien calibrée, inspecter est rentable dès que la probabilité de dégradation dépasse 150 / (2 500 − 300 + 150) ≈ 0,064. Chaque dégradation détectée à temps fait économiser 2 200 € (2 500 − 300), ce qui compense environ 15 fausses alertes.

### 6.2 Recherche du seuil sur la validation

Pour chaque modèle, 99 seuils de 0,01 à 0,99 sont testés sur le jeu de validation, et celui qui minimise le coût total est retenu. Le modèle est ensuite figé : le seuil n'est plus modifié.

| Modèle | AUC validation | AP validation | Seuil retenu | Coût validation |
|---|---|---|---|---|
| Régression logistique | 0,804 | 0,203 | 0,07 | 112 400 € |
| Gradient Boosting | 0,837 | 0,477 | 0,05 | 85 300 € |

Le Gradient Boosting est meilleur sur tous les critères de validation, et c'est donc lui qui est retenu comme modèle final. Les deux seuils sont proches du seuil théorique de 0,064 ; ils lui sont légèrement inférieurs, ce qui est cohérent avec la part de positifs plus élevée en validation (1,9 %) qu'en entraînement et avec la dérive décrite plus loin.

### 6.3 Pourquoi le seuil doit être choisi avant l'évaluation finale

Le seuil est un paramètre estimé à partir de données. Si on le choisit en regardant le jeu de test, on retient le seuil qui s'ajuste le mieux au hasard de ce jeu particulier : le coût obtenu devient trop optimiste et n'est plus une estimation du coût en exploitation. En situation réelle, la décision d'inspecter se prend avant de connaître l'avenir ; le jeu de test, placé après la validation dans le temps, simule précisément cet avenir. La validation sert donc de terrain de réglage et le test de verdict, et ce verdict n'est honnête que s'il n'a servi à rien d'autre.

Les résultats illustrent cet effet. Sur la validation, où le seuil a été optimisé, le Gradient Boosting réduit le coût d'environ 57 % par rapport à « ne jamais inspecter » (estimation d'environ 79 cas positifs, soit un coût de référence voisin de 197 000 €). Sur le test, où le seuil est appliqué une seule fois, la réduction n'est plus que de 20 %. Le chiffre du test est celui qu'il faut retenir.

## 7. Résultats sur le jeu de test

Le seuil de chaque modèle, fixé sur la validation, a été appliqué une seule fois au jeu de test, qui contient 118 cas à risque et 3 693 cas sains.

### 7.1 Matrices de confusion

Régression logistique (seuil 0,07) :

| | Prédit sain | Prédit à risque |
|---|---|---|
| Réel sain | 3 179 | 514 |
| Réel à risque | 67 | 51 |

Gradient Boosting (seuil 0,05) :

| | Prédit sain | Prédit à risque |
|---|---|---|
| Réel sain | 3 588 | 105 |
| Réel à risque | 84 | 34 |

### 7.2 Indicateurs et coûts

| Modèle | Rappel | Précision | Part inspectée | Coût faux négatifs | Coût fausses alertes | Coût détections | **Coût total** |
|---|---|---|---|---|---|---|---|
| Régression logistique | 43,2 % | 9,0 % | 14,8 % | 167 500 € | 77 100 € | 15 300 € | **259 900 €** |
| Gradient Boosting | 28,8 % | 24,5 % | 3,6 % | 210 000 € | 15 750 € | 10 200 € | **235 950 €** |

### 7.3 Comparaison avec des politiques de référence

| Politique | Coût sur le test | Écart avec le Gradient Boosting |
|---|---|---|
| Toujours inspecter | 589 350 € | −353 400 € (−60 %) |
| Ne jamais inspecter | 295 000 € | −59 050 € (−20 %) |
| Régression logistique | 259 900 € | −23 950 € (−9 %) |
| **Gradient Boosting** | **235 950 €** | – |

Le Gradient Boosting est la meilleure politique, avec une économie de 59 050 € par rapport à l'absence d'inspection. Ce gain se vérifie par le calcul : 34 détections à 2 200 € d'économie moins 105 fausses alertes à 150 € donnent bien 59 050 €. La régression logistique détecte davantage de cas (51 contre 34), mais au prix de 514 fausses alertes, soit environ dix par détection, ce qui absorbe presque tout son bénéfice, alors que le Gradient Boosting n'en émet que trois par détection.

## 8. Interprétation : variables les plus importantes

### 8.1 Gradient Boosting (importance par permutation sur la validation)

| Variable | Importance |
|---|---|
| Performance moyenne hors éclipse (3 dernières heures) | 0,449 |
| Variation de la performance sur 1 h | 0,041 |
| Ratio de vie de l'équipement | 0,031 |
| Variation de la puissance sur 1 h | 0,018 |
| Performance instantanée | 0,015 |
| Tension | 0,015 |
| Puissance instantanée | 0,010 |
| Écart-type de la performance sur 1 h | 0,004 |
| Écart-type de la température sur 1 h | 0,003 |
| Rayonnement | 0,003 |

### 8.2 Régression logistique (coefficients standardisés, premiers par valeur absolue)

| Variable | Coefficient |
|---|---|
| Performance moyenne hors éclipse (3 h) | −1,306 |
| Type batterie | −0,425 |
| Écart-type de la performance (1 h) | +0,369 |
| Type convertisseur DC | +0,316 |
| Performance moyenne (1 h) | +0,238 |
| Écart-type de la puissance (1 h) | −0,212 |

### 8.3 Lecture

La variable dominante est de loin la performance hors éclipse des 3 dernières heures : plus elle est élevée, plus le risque est faible. Ce résultat est cohérent avec la définition de la cible, puisque l'événement à prédire est un franchissement du seuil de 0,65 par la performance : un équipement dont la performance récente s'approche du seuil a plus de chances de le franchir. La variation de la performance sur 1 h et l'instabilité de la performance (écart-type sur 1 h) arrivent ensuite et signalent une baisse en cours. Les batteries sont moins exposées, ce qui est cohérent avec leur taux de positifs de 0,9 %, contre 2,6 % pour les convertisseurs et 4,0 % pour les panneaux.

La température, la tension, le courant et les alarmes récentes pèsent peu. Cela ne signifie pas qu'ils sont sans effet physique : ces grandeurs sont corrélées entre elles et avec la performance, l'importance par permutation sous-estime les variables redondantes, et le jeu de validation ne compte qu'environ 79 cas positifs, ce qui rend le classement des variables secondaires instable. Dans la régression logistique, les coefficients de variables fortement corrélées (moyenne et écart-type de la performance) se compensent et ne doivent pas être interprétés isolément.

Le ratio de vie de l'équipement apparaît parmi les variables utiles, mais il est constant pour un équipement donné : il peut jouer le rôle d'identifiant et ne se généraliserait pas à un équipement nouveau.

## 9. Limites et précautions

**Taille et indépendance des données.** L'étude couvre 45 jours, et chaque jeu contient quelques dizaines à une centaine de cas positifs, issus d'observations horaires d'un même équipement qui ne sont pas indépendantes. L'écart de coût de 59 050 € est un ordre de grandeur, pas un chiffre précis. Un rééchantillonnage par équipement permettrait d'en estimer l'incertitude.

**Dérive temporelle.** La part de positifs varie fortement selon la semaine, de 0,7 % (2 au 8 juin) à 4,5 % (9 au 15 juin). Le test tombe sur une période où les cas à risque sont plus fréquents que pendant l'entraînement, ce qui explique que le rappel du Gradient Boosting au test (29 %) soit modeste malgré une bonne qualité en validation.

**Définition de la performance.** La performance, rapport entre puissance et puissance nominale, dépend de l'éclairement. La performance médiane hors éclipse des panneaux (0,54) est inférieure au seuil de 0,65, ce qui explique que 42 % des dates soient écartées comme « déjà dégradées » : un panneau faiblement éclairé peut franchir le seuil sans avoir de défaut. Rapporter la puissance à la puissance attendue pour l'éclairement mesuré (ou fixer un seuil par type d'équipement) donnerait une cible plus proche de l'état réel du matériel.

**Procédure de développement.** La définition de la cible a été affinée en plusieurs itérations (restriction aux équipements sains, exclusion des batteries du critère de performance, traitement des valeurs aberrantes). Ces choix s'appuient sur des diagnostics calculés sur l'ensemble de la période, test compris, et ont été faits après observation de résultats antérieurs. Le test final n'est donc pas totalement vierge, et une validation sur des données futures indépendantes serait nécessaire avant tout déploiement.

**Alarmes.** Elles pèsent peu dans le modèle ; leur contribution dépend de la qualité du rattachement entre les alarmes et la télémétrie.

## 10. Conclusion et pistes

Avec une cible qui décrit l'entrée en dégradation et un seuil de décision réglé sur la validation, le Gradient Boosting réduit le coût d'environ 20 % par rapport à l'absence d'inspection et de 60 % par rapport à une inspection systématique, en n'inspectant que 3,6 % des équipements. La régression logistique, qui inspecte beaucoup plus, apporte un bénéfice nettement plus faible. Le modèle s'appuie surtout sur la performance récente et sur ses variations, ce qui est cohérent avec la définition de la cible.

Ces résultats sont encourageants mais fragiles, compte tenu de la durée limitée des données. Les pistes d'amélioration les plus utiles sont : définir la performance relative à l'éclairement, étalonner les probabilités sur la validation avant de chercher le seuil, estimer l'incertitude des coûts par rééchantillonnage par équipement, et réentraîner le modèle sur une période plus longue pour mieux gérer la dérive.

---

## Annexe – Reproductibilité

Le script `maintenance_predictive.py` lit la base DuckDB, construit la cible et les variables, réalise le découpage chronologique, entraîne les deux modèles, cherche le seuil sur la validation, évalue une seule fois sur le test et affiche les importances. Ses principaux paramètres sont l'horizon (9 h), le seuil de performance (0,65), la liste des types d'équipement pour lesquels la performance est évaluée, la limite de validité de la performance (1,5), les alarmes ciblées (`perte_puissance`, `surchauffe`), les sévérités critiques (`critical`, `URGENCE`), la matrice de coûts et l'option de restriction aux équipements sains.
