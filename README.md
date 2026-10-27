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
scripts/              vérifications et statistiques rapides
tests/
```

## Sources de données

- [Vélib Métropole – disponibilité temps réel](https://transport.data.gouv.fr/datasets/velib-velos-et-bornes-disponibilite-temps-reel) (licence ODbL)
- [PRIM – Île-de-France Mobilités](https://prim.iledefrance-mobilites.fr) (licence Mobilités)
