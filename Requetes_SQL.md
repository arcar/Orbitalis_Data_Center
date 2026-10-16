## Quelle est la production totale par équipement et par jour ?
```
SELECT
    fp.equipement_id,
    dt.date AS jour,
    SUM(fp.puissance_w) AS puissance_totale_w
FROM base_etoile.main.fait_production AS fp
JOIN base_etoile.main.dim_temps AS dt ON dt.temps_id = fp.temps_id
GROUP BY fp.equipement_id, dt.date
ORDER BY fp.equipement_id, dt.date;
```


## Quels équipements présentent les performances moyennes les plus faibles (hors périodes d’éclipse) ?

```
SELECT
    fp.equipement_id,
    de.type_equipement,
    de.site,
    ROUND(AVG(fp.performance), 2) AS performance_moyenne,
    COUNT(*) AS nb_mesures
FROM base_etoile.main.fait_production AS fp
JOIN base_etoile.main.dim_equipement AS de ON de.equipement_id = fp.equipement_id
WHERE fp.phase = 'ensoleillement'
GROUP BY fp.equipement_id, de.type_equipement, de.site
ORDER BY performance_moyenne ASC;
```

## Combien d’alarmes chaque équipement a-t-il générées, par niveau de sévérité ?

```
SELECT fp.equipement_id, da.severite, COUNT(fp.alarme_id) AS nb_alarme_total FROM fait_production fp
LEFT JOIN dim_alarme da ON da.alarme_id =  fp.alarme_id
WHERE da.alarme_id IS NOT NULL
GROUP BY fp.equipement_id, da.severite
ORDER BY fp.equipement_id;
```

## Quel est le délai moyen entre deux opérations de maintenance pour un même équipement ?
```
WITH debuts AS (
    SELECT
        fp.equipement_id,
        fp.maintenance_id,
        MIN(strptime(CAST(fp.temps_id AS VARCHAR), '%Y%m%d%H%M')) AS debut
    FROM base_etoile.main.fait_production AS fp
    WHERE fp.maintenance_id IS NOT NULL
    GROUP BY fp.equipement_id, fp.maintenance_id
),
ecarts AS (
    SELECT
        equipement_id,
        date_diff('minute',
                  LAG(debut) OVER (PARTITION BY equipement_id ORDER BY debut),
                  debut) AS delai_min
    FROM debuts
)
SELECT equipement_id, ROUND(AVG(delai_min) / 1440.0, 1) AS delai_moyen_jours
FROM ecarts
WHERE delai_min IS NOT NULL
GROUP BY equipement_id
ORDER BY equipement_id;
```

## Quels équipements n’ont pas été maintenus depuis plus de 90 jours ?

```
WITH ref AS (
    SELECT MAX(strptime(CAST(temps_id AS VARCHAR), '%Y%m%d%H%M')) AS date_ref
    FROM base_etoile.main.fait_production
),
derniere_maintenance AS (
    SELECT
        fp.equipement_id,
        MAX(strptime(CAST(fp.temps_id AS VARCHAR), '%Y%m%d%H%M')) AS derniere
    FROM base_etoile.main.fait_production AS fp
    WHERE fp.maintenance_id IS NOT NULL
    GROUP BY fp.equipement_id
)
SELECT
    de.equipement_id,
    de.type_equipement,
    de.site,
    dm.derniere AS derniere_maintenance,
    date_diff('day', dm.derniere, ref.date_ref) AS jours_depuis
FROM base_etoile.main.dim_equipement AS de
CROSS JOIN ref
LEFT JOIN derniere_maintenance AS dm ON dm.equipement_id = de.equipement_id
WHERE dm.derniere IS NULL
   OR date_diff('day', dm.derniere, ref.date_ref) > 90
ORDER BY jours_depuis DESC NULLS FIRST;
```
## Quelle est la perte de production estimée par site (en kWh) ?
```

SELECT
    de.site,
    ROUND(SUM(fp.perte_production) FILTER (WHERE fp.phase = 'ensoleillement') / 12000.0, 2) AS perte_totale_kwh
FROM base_etoile.main.fait_production AS fp
JOIN base_etoile.main.dim_equipement AS de ON de.equipement_id = fp.equipement_id
GROUP BY de.site
ORDER BY perte_totale_kwh DESC;
```
/12000 car 12 pas de 5 minutes dans une heure et * 1000 pour obtenir des Kwh


## Comment évolue la performance d’un équipement au cours du temps ? (utiliser au moins une fonction de fenêtre)
```
SELECT
    fp.equipement_id,
    dt.date,
    dt.heure,
    dt.minute,
    fp.phase,
    fp.performance,
    fp.performance - LAG(fp.performance) OVER (PARTITION BY fp.equipement_id ORDER BY dt.temps_id) AS evolution_performance
FROM fait_production fp
JOIN dim_temps dt ON fp.temps_id = dt.temps_id
WHERE fp.phase LIKE 'ensoleillement'
ORDER BY fp.equipement_id, dt.temps_id;
```

## Comparer les performances pendant les phases d’ensoleillement et les phases d’éclipse sans considérer l’éclipse comme une panne.
```

```

## Quel est le coût estimé des pertes de production non anticipées sur la période disponible ?

```

```
