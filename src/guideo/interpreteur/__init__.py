"""Interpréteur d'activités : phrase dite à l'oral -> événement d'agenda (lieu + créneau).

- comprendre.py : le LLM (Ollama) identifie le sens de la phrase, sans voir la mémoire ;
- texte.py      : le code lit jour, heures et mots exacts dans la phrase ;
- analyse.py    : le code complète avec la mémoire personnelle et les connaissances générales ;
- cinema.py     : recherche de la vraie séance ;
- geo.py        : géocodage (IGN, OpenStreetMap).
"""

from .analyse import Analyse, Manque, analyser, appliquer_horaire, completer, expliquer

__all__ = ["Analyse", "Manque", "analyser", "appliquer_horaire", "completer", "expliquer"]
