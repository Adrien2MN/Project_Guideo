"""Smoke test of your PRIM key: one journey from La Défense to Châtelet.

Usage:  python scripts/check_prim.py
"""

from guideo.config import load_settings
from guideo.prim.client import PrimClient, PrimError

LA_DEFENSE = (2.2385, 48.8919)   # (lon, lat)
CHATELET = (2.3470, 48.8584)


def main() -> None:
    settings = load_settings()
    try:
        client = PrimClient(settings.prim_api_key or "")
        data = client.journeys(LA_DEFENSE, CHATELET)
    except PrimError as exc:
        print(f"ERREUR : {exc}")
        return

    journeys = data.get("journeys", [])
    print(f"OK, {len(journeys)} itinéraire(s) reçu(s).\n")
    for j in journeys[:3]:
        print(f"- {j.get('type', '?')} : {j.get('duration', 0) // 60} min, "
              f"{j.get('nb_transfers', '?')} correspondance(s)")
        for s in j.get("sections", []):
            info = s.get("display_informations", {})
            label = f"{info.get('commercial_mode', '')} {info.get('code', '')}".strip()
            print(f"    {s.get('type'):<18} {s.get('duration', 0) // 60:>3} min  {label}")
    client.close()


if __name__ == "__main__":
    main()
