"""Quick look at what the collector has stored so far.

Usage:  python scripts/velib_stats.py
"""

import sqlite3
from datetime import datetime

from guideo.config import load_settings


def fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def main() -> None:
    db = load_settings().velib_db_path
    if not db.exists():
        print(f"Pas encore de base à {db}. Lance d'abord le collecteur.")
        return
    conn = sqlite3.connect(db)
    n_polls, first, last = conn.execute(
        "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM polls").fetchone()
    n_events = conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0]
    n_stations = conn.execute("SELECT COUNT(*) FROM station_info").fetchone()[0]

    print(f"Base         : {db} ({db.stat().st_size / 1e6:.1f} Mo)")
    print(f"Stations     : {n_stations}")
    print(f"Polls        : {n_polls}  (du {fmt(first)} au {fmt(last)})")
    print(f"Événements   : {n_events}")

    # Gaps longer than 5 minutes = the collector was not running
    gaps = conn.execute("""
        SELECT prev, observed_at FROM (
            SELECT observed_at, LAG(observed_at) OVER (ORDER BY observed_at) AS prev
            FROM polls)
        WHERE observed_at - prev > 300 ORDER BY observed_at DESC LIMIT 5
    """).fetchall()
    if gaps:
        print("\nDernières coupures (> 5 min) :")
        for a, b in gaps:
            print(f"  {fmt(a)} -> {fmt(b)}  ({(b - a) // 60} min)")

    print("\nStations vides en ce moment (aucun vélo) :",
          conn.execute("""
            SELECT COUNT(*) FROM status_events e
            JOIN (SELECT station_id, MAX(observed_at) t FROM status_events
                  GROUP BY station_id) l
              ON e.station_id = l.station_id AND e.observed_at = l.t
            WHERE e.mechanical + e.ebike = 0 AND e.is_installed = 1
          """).fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
