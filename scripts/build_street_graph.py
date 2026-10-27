"""Download the walk and bike street graphs and add elevations (run once).

Usage:  python scripts/build_street_graph.py
        python scripts/build_street_graph.py --check   (only tests the IGN API)

Takes several minutes the first time (OpenStreetMap download + ~1 IGN request per
100 intersections). If it stops halfway, run it again: elevations are cached.
"""

import sys
import time

from guideo.config import load_settings
from guideo.streets.build import add_elevations, download, save_graph
from guideo.streets.elevation import IgnElevationClient

TEST_POINTS = {  # (lon, lat): expected rough elevation
    "Trocadéro": ((2.286840, 48.863088), "~60 m"),
    "Javel (bord de Seine)": ((2.278037, 48.847162), "~30 m"),
}


def check(client: IgnElevationClient) -> bool:
    try:
        values = client.query([p for p, _ in TEST_POINTS.values()])
    except Exception as exc:
        print(f"ERREUR API IGN : {type(exc).__name__}: {exc}")
        return False
    for (name, (_, expected)), z in zip(TEST_POINTS.items(), values):
        print(f"  {name:<22} {z} m   (attendu {expected})")
    return all(z is not None for z in values)


def main() -> None:
    settings = load_settings()
    client = IgnElevationClient(settings.streets_dir / "elevations.sqlite")
    print("Test de l'API altimétrique IGN :")
    if not check(client) or "--check" in sys.argv:
        client.close()
        return

    for network in ("bike", "walk"):
        path = settings.streets_dir / f"{network}.pkl"
        if path.exists() and "--force" not in sys.argv:
            print(f"\n[{network}] déjà construit ({path}), ignoré. --force pour le refaire.")
            continue
        t0 = time.time()
        print(f"\n[{network}] téléchargement OpenStreetMap...")
        G = download(network)
        print(f"[{network}] {G.number_of_nodes()} intersections, {G.number_of_edges()} tronçons")
        fixed = add_elevations(G, client)
        print(f"[{network}] altitudes ajoutées ({fixed} points de ponts/tunnels réinterpolés)")
        save_graph(G, path)
        print(f"[{network}] enregistré dans {path} ({time.time() - t0:.0f} s)")
    client.close()


if __name__ == "__main__":
    main()
