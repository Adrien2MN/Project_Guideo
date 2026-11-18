"""Diagnostic : pourquoi Allociné renvoie-t-il une erreur 403 sur les séances ?

Utilisation (racine du projet, venv activé) :
    python scripts/diagnostic_allocine.py
    python scripts/diagnostic_allocine.py --cinema "UGC Montparnasse" --date 2026-11-20

Fait une seule requête, identique à celle de la bibliothèque allocine-seances, et affiche
le code de réponse, le serveur et les indices d'une protection anti-robots. Ne contourne rien.
"""

import argparse
import json
from datetime import date
from pathlib import Path

import requests
from allocineAPI.allocineAPI import URLs

parser = argparse.ArgumentParser()
parser.add_argument("--cinema", default="UGC Montparnasse")
parser.add_argument("--date", default=date.today().isoformat())
args = parser.parse_args()

cache = json.loads(Path("data/cinemas_cache.json").read_text(encoding="utf-8"))
cinemas = cache["cinemas"] if isinstance(cache, dict) else cache
cinema = next((c for c in cinemas if args.cinema.lower() in c["nom"].lower()), None)
if cinema is None:
    raise SystemExit(f"{args.cinema!r} absent de data/cinemas_cache.json")

url = URLs.showtime_url(cinema["id"], args.date, 1)
print(f"Cinéma : {cinema['nom']} ({cinema['id']})\nURL    : {url}")
r = requests.get(url, timeout=15)
corps = r.text[:3000].lower()
print(f"Code   : {r.status_code}")
print(f"Serveur: {r.headers.get('server')} | type : {r.headers.get('content-type')}")
indices = [m for m in ("captcha", "datadome", "cloudflare", "cf-ray", "access denied", "forbidden", "bot")
           if m in corps or m in str(r.headers).lower()]
print(f"Indices de protection anti-robots : {indices or 'aucun'}")
if r.status_code == 200:
    try:
        d = r.json()
        print(f"OK : {len(d.get('results', []))} film(s) sur la page 1")
    except ValueError:
        print("Réponse 200 mais pas du JSON : le format a changé")
else:
    print("Début de la réponse :")
    print(r.text[:400])
