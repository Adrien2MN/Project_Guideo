# Prédicteur Vélib à la station — v0

Prévoit, pour chaque station et à 15 / 30 / 60 min :
- le nombre de vélos mécaniques, de vélos électriques et de places libres ;
- la probabilité d'avoir **au moins un** vélo mécanique / électrique / une place (sortie à privilégier pour le routage Guideo).

## Utilisation

```bash
pip install lightgbm pandas pyarrow scikit-learn
python prepare.py velib.sqlite grid.parquet           # journal → grille 5 min
python train.py grid.parquet velib.sqlite out --final  # évaluation + modèles finaux dans out/
python predict.py velib.sqlite out 6245 6293           # prévision à l'instant le plus récent
```

`predict.py` lit directement la base du collecteur (il lui faut au moins la dernière heure pour les tendances). Sans identifiant de station, il prédit les 1 520 stations.

## Fonctionnement

- **Grille** : état de chaque station toutes les 5 min, uniquement aux instants où le collecteur tournait (table `polls`). Les trous restent des trous.
- **Variables** : état actuel, variations sur 15/30/60 min, capacité, remplissage, ancienneté du dernier rapport de la station, heure, jour, jour férié, vacances scolaires, remplissage et tendance des 6 stations voisines, profil historique de la station au moment visé.
- **Modèles** : LightGBM global (toutes stations), un par cible × horizon. Régression L1 sur la variation, classifieur binaire pour « ≥ 1 ».
- **Validation** : découpage temporel strict (entraînement jusqu'au 02/11 13 h, calage jusqu'au 03/11, test du 03/11 après-midi au 05/11).

## Résultats sur le test (erreur absolue moyenne, persistance → modèle)

| | 15 min | 30 min | 60 min |
|---|---|---|---|
| Vélos méca | 0,76 → 0,64 | 1,05 → 0,92 | 1,30 → 1,30 |
| Vélos élec | 0,85 → 0,78 | 1,22 → 1,13 | 1,62 → 1,62 |
| Places | 0,95 → 0,95 | 1,46 → 1,46 | 2,22 → 2,21 |

Probabilités « ≥ 1 » : score de Brier amélioré de 30 à 43 % par rapport à la persistance à tous les horizons, AUC 0,76–0,99, bonne calibration.

## Limites connues

- **Aucun matin de semaine (5 h–11 h) dans les données** : prévisions de la pointe du matin non fiables.
- ~5,4 jours utiles, dont une partie pendant les vacances de la Toussaint.
- Régression des places et horizon 60 min ≈ persistance. Une perte L2 faisait mieux à 60 min en heure de pointe (−10 %) mais moins bien la nuit ; à revoir avec plus de données (perte de Huber, choix par horizon sur la période de calage).
- Calendrier des fériés / vacances codé en dur dans `features.py`, à compléter.
