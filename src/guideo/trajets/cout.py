"""Fonction de coût : comment Guideo compare des trajets qui n'ont pas la même « pénibilité ».

Le coût s'exprime en minutes équivalentes, ajoutées à la durée réelle :
- une correspondance coûte plus que son temps de marche (descendre, changer de quai,
  incertitude) ;
- un Vélib dont on ne sait pas s'il y aura un vélo au départ ou une place à l'arrivée
  est un risque.

La sélection garde ensuite le trajet qui part le plus tard (mode « arriver à ») ou qui
arrive le plus tôt (mode « partir à »), une fois ces pénalités comptées. Les horaires
affichés restent les vrais horaires : la pénalité ne sert qu'à choisir.

Les valeurs ci-dessous sont des premières estimations à la main, à remplacer par un
modèle appris sur tes choix réels (modèle de choix modal).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ParametresCout:
    correspondance_min: float = 5.0     # par changement entre deux lignes de transport
    velib_inconnu_min: float = 2.0      # stations non vérifiables (trajet lointain, pas de prédicteur)
    velib_echec_min: float = 10.0       # ce que coûte l'absence de vélo ou de place (× probabilité d'échec)
    velo_confort_min: float = 10.0      # minutes de pédalage « gratuites »
    velo_effort: float = 0.5            # au-delà, chaque minute de vélo compte 1 + velo_effort


def penalite_min(correspondances: int, proba_velib: Optional[float], avec_velib: bool,
                 p: ParametresCout = ParametresCout(), velo_min: float = 0.0) -> float:
    """Pénalité en minutes d'une option.

    `proba_velib` : probabilité que tout se passe bien côté Vélib (vélo au départ ET place
    à l'arrivée), ou None si on ne sait pas. `velo_min` : minutes passées à pédaler.
    """
    pen = p.correspondance_min * max(correspondances, 0)
    if avec_velib:
        pen += p.velib_inconnu_min if proba_velib is None else p.velib_echec_min * (1 - proba_velib)
        pen += p.velo_effort * max(velo_min - p.velo_confort_min, 0)
    return pen
