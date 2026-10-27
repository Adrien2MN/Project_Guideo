"""Does the slope make your trips asymmetric? Compare both directions of key legs.

Usage:  python scripts/compare_bike.py
"""

import tomllib
from pathlib import Path

from guideo.config import load_settings
from guideo.streets.build import load_graph
from guideo.streets.router import StreetRouter
from guideo.streets.speed import EBIKE, MECHANICAL, WALK

ROOT = Path(__file__).resolve().parent.parent

# Stop coordinates as given by Navitia (lon, lat)
STOPS = {
    "Javel (RER C)": (2.278037, 48.847162),
    "Champ de Mars (RER C)": (2.289656, 48.856477),
    "Passy (M6)": (2.285731, 48.857487),
    "Trocadéro (M6)": (2.286840, 48.863088),
}


def main() -> None:
    settings = load_settings()
    trips = tomllib.loads((ROOT / "scripts" / "test_trips.toml").read_text(encoding="utf-8"))
    home = tuple(trips["trip"][0]["from"])  # start of your first reference trip

    print("Chargement des graphes...")
    bike = StreetRouter(load_graph(settings.streets_dir / "bike.pkl"))
    walk_path = settings.streets_dir / "walk.pkl"
    walk = StreetRouter(load_graph(walk_path)) if walk_path.exists() else None
    if walk is None:
        print("(graphe piéton absent : la marche n'est pas affichée)")

    for name, stop in STOPS.items():
        for label, a, b in ((f"Maison -> {name}", home, stop), (f"{name} -> Maison", stop, home)):
            r = bike.route(a, b, MECHANICAL)
            print(f"\n{label}   (altitude {r.start_elevation_m:.0f} m -> {r.end_elevation_m:.0f} m)")
            print("  " + r.summary())
            print("  " + bike.route(a, b, EBIKE).summary())
            if walk is not None:
                print("  " + walk.route(a, b, WALK).summary())


if __name__ == "__main__":
    main()
