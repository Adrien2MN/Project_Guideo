# Guideo

Planificateur d'itinéraires multimodal et personnalisé pour Paris (Vélib, transports IDFM via PRIM), avec intégration à l'agenda.

> Projet portfolio en cours. Phase actuelle : **0 — collecte des données**.

## Installation (Windows / Anaconda)

Dans **Anaconda Prompt**, depuis le dossier du projet :

```bat
conda create -n guideo python=3.12 -y
conda activate guideo
pip install -e ".[dev,analysis]"
copy .env.example .env
```

Ouvre ensuite `.env` et colle ta clé PRIM après `PRIM_API_KEY=`.

## Vérifier que tout marche

```bat
pytest
python scripts\check_prim.py
```

`check_prim.py` demande un itinéraire La Défense → Châtelet. Si tu vois une liste de trajets avec leurs étapes, ta clé fonctionne.

## Lancer le collecteur Vélib

```bat
python -m guideo.velib.collector
```

Il interroge le flux Vélib chaque minute et enregistre dans `data\velib.sqlite` uniquement les stations dont l'état a changé (vélos mécaniques, électriques, places libres). Laisse la fenêtre ouverte ; `Ctrl+C` pour l'arrêter. Tu peux le relancer à tout moment : il reprend là où il en était.

Pour voir ce qui a été collecté :

```bat
python scripts\velib_stats.py
```

Les journaux sont dans `data\logs\`.

### Le lancer automatiquement à l'ouverture de session (optionnel)

1. Crée un fichier `start_collector.bat` contenant :
   ```bat
   call %USERPROFILE%\anaconda3\Scripts\activate.bat guideo
   cd /d C:\chemin\vers\guideo
   python -m guideo.velib.collector
   ```
2. Ouvre le **Planificateur de tâches** Windows → *Créer une tâche de base* → déclencheur *À l'ouverture de session* → action *Démarrer un programme* → ce fichier `.bat`.

Pense aussi à empêcher la mise en veille si tu veux des données continues.

## Réseau des rues et relief

Guideo calcule lui-même les trajets à pied et à vélo, car l'API Navitia de PRIM ne gère pas le vélo. Le temps de chaque tronçon de rue dépend de sa pente, dans chaque sens, et du type de vélo (`src/guideo/streets/speed.py`).

À faire une fois (quelques minutes) :

```bat
pip install -e ".[streets]"
python scripts\build_street_graph.py --check
python scripts\build_street_graph.py
```

Le premier appel vérifie seulement l'API altimétrique de l'IGN. Le second télécharge les rues depuis OpenStreetMap, ajoute l'altitude de chaque intersection et enregistre les graphes dans `data\streets\`.

Puis, pour voir l'effet du relief sur tes trajets :

```bat
python scripts\compare_bike.py
```

## Interpréteur d'activités et mémoire personnelle

Une phrase comme « je grimpe à Issy lundi soir » devient un événement d'agenda (lieu + créneau), qui servira ensuite au calcul du trajet.

```bat
pip install -e ".[interpreteur]"
ollama pull ministral-3:8b-instruct-2512-q4_K_M
python scripts\test_interpreteur.py --discuter
```

- **Le LLM (local, via Ollama) comprend le sens** : quelle activité, quels mots désignent le lieu, la personne, le film. Il ne voit jamais la mémoire personnelle, donc aucune adresse.
- **Le code fait le reste** : il lit le jour et l'heure dans la phrase, retrouve le lieu ou le contact dans la mémoire, complète les horaires et pose une question quand il manque quelque chose.
- **La mémoire commence vide** (`data\memoire\memoire.json`, hors git) et se remplit avec tes réponses, tes choix et tes corrections. Les habitudes y sont stockées comme des observations (une par activité validée), dont on tire l'heure et la durée habituelles. Chaque modification est tracée dans `data\memoire\journal.jsonl`.
- **Les connaissances générales** (une soirée commence vers 20h, un déjeuner vers midi...) sont dans `src\guideo\memoire\connaissances_generales.toml`. Elles ne servent que tant qu'aucune habitude personnelle n'existe.

Évaluation, avec une mémoire de test fixe (`tests\eval\memoire_eval.json`) pour que les scores soient comparables :

```bat
python scripts\eval_interpreteur.py -v
```

**Données personnelles.** La mémoire contient des adresses de tiers (tes contacts) : elle reste sur la machine et n'est jamais envoyée au LLM. Tout est lisible et modifiable dans le fichier, et `Memoire.supprimer()` efface une entité avec ses observations.

Les séances de cinéma viennent d'une bibliothèque non officielle qui lit le site d'Allociné : usage personnel uniquement.

## Agenda et trajets autour des événements

Quand un événement est validé dans `test_interpreteur.py --discuter`, il entre dans l'agenda (`data\agenda\agenda.json`) avec un trajet aller et un trajet retour, et `data\agenda\guideo.ics` est réécrit (à importer ou à abonner dans ton agenda : les trajets y bloquent le temps de déplacement).

- **Niveau 1** : sans autre événement autour, départ de chez toi et retour chez toi.
- **Niveau 2** : si un événement finit peu avant (moins de 6 h), on part de là-bas. Si l'écart laisse au moins 30 min à la maison, Guideo te fait repasser chez toi, sinon il calcule le trajet direct. Si même le trajet direct arrive après le début, l'événement porte une alerte. Toute la journée est recalculée à chaque ajout : le trajet retour de l'événement précédent change en conséquence.
- **Calcul des trajets** (`src\guideo\trajets`) : PRIM pour les transports en commun (clé `PRIM_API_KEY`), les graphes des rues pour le vélo et la marche s'ils sont construits, sinon une estimation à vol d'oiseau, signalée comme telle. Le plus rapide est retenu, les autres sont cités.
- Les réglages (marge d'arrivée, temps minimal à la maison, fenêtre de 6 h) sont dans `connaissances_generales.toml`, section `[trajets]`.

```bat
python scripts\agenda.py                       :: les 7 prochains jours
python scripts\agenda.py --supprimer ID        :: retire un événement et recalcule la journée
python scripts\agenda.py --replanifier 2026-11-20
```

## Le modèle de données

| Table | Contenu |
|---|---|
| `station_info` | Nom, position, capacité de chaque station (rafraîchi chaque jour) |
| `status_events` | Un enregistrement par changement d'état d'une station |
| `polls` | Chaque interrogation réussie du flux |

**Important pour la modélisation :** une station sans événement pendant une période *et* couverte par des `polls` n'a pas changé. Une période sans `polls` est une donnée manquante (PC éteint, coupure réseau), pas une station stable.

## Structure

```
src/guideo/
  config.py           paramètres (.env)
  velib/client.py     lecture du flux GBFS Vélib
  velib/storage.py    stockage SQLite en journal d'événements
  velib/collector.py  boucle de collecte
  prim/client.py      client de l'API Navitia de PRIM
  memoire/            mémoire personnelle + connaissances générales
  interpreteur/       phrase -> événement (LLM, lecture du texte, cinéma, géocodage)
  trajets/            trajet entre deux points (PRIM, rues, estimation)
  agenda/             agenda local, trajets aller/retour, planification de la journée, export .ics
scripts/              vérifications et statistiques rapides
tests/
```

## Sources de données

- [Vélib Métropole – disponibilité temps réel](https://transport.data.gouv.fr/datasets/velib-velos-et-bornes-disponibilite-temps-reel) (licence ODbL)
- [PRIM – Île-de-France Mobilités](https://prim.iledefrance-mobilites.fr) (licence Mobilités)
