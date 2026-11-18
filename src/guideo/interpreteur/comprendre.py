"""Le LLM : comprendre le SENS de la phrase, rien de plus.

Il ne reçoit PAS la mémoire personnelle (ni lieux, ni contacts, ni adresses) : il dit seulement
quelle activité, et quels mots désignent le lieu, la personne chez qui on va, les participants
et le film. Dates, heures, lieux exacts et habitudes sont traités par le code ensuite.
C'est plus fiable avec un petit modèle, et c'est la minimisation des données du RGPD.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

MODELE = "ministral-3:8b-instruct-2512-q4_K_M"

Activite = Literal["escalade", "soiree", "repas", "visite", "sport", "cinema", "spectacle",
                   "travail", "rendez-vous", "autre"]


class Comprehension(BaseModel):
    raisonnement: str = Field(description="Une phrase : quelle activité et quels mots désignent le lieu, la personne, le film")
    activite: Activite
    titre: str = Field(description="Titre court pour l'agenda, ex. 'Escalade', 'Dîner chez Paul'")
    zone: Optional[str] = Field(description="Mots EXACTS désignant le lieu, le quartier ou la ville (sans 'à', 'au'), ou null")
    chez: Optional[str] = Field(description="Personne chez qui a lieu l'activité, mots exacts après 'chez', ou null")
    avec: list[str] = Field(description="Personnes qui participent (après 'avec'), mots exacts ; liste vide sinon")
    film: Optional[str] = Field(description="Titre du film, mots exacts, sans le corriger ; null si ce n'est pas du cinéma")

    @field_validator("zone", "chez", "film", mode="before")
    @classmethod
    def vide_vers_null(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("avec", mode="before")
    @classmethod
    def liste(cls, v):
        if v is None:
            return []
        return [v] if isinstance(v, str) else v


PROMPT = """Tu es l'interpréteur d'agenda de l'application Guideo.
L'utilisateur dit à l'oral une activité qu'il va faire. Tu identifies le SENS de sa phrase.

Activités :
- escalade : grimper, bloc, voie, escalade
- soiree : soirée, fête, apéro chez quelqu'un
- repas : déjeuner, dîner, resto, brunch, manger
- visite : passer chez quelqu'un, voir sa famille
- sport : course, foot, piscine, salle de sport, vélo
- cinema : ciné, film, mater/voir un film
- spectacle : théâtre, concert, expo
- travail : boulot, bureau, réunion
- rendez-vous : médecin, coiffeur, banque, administration
- autre : tout le reste

Règles :
- Recopie les mots EXACTS de l'utilisateur pour zone, chez, avec et film. Ne corrige jamais un nom.
- Ne déduis rien qui n'est pas dit : pas de lieu, pas de personne inventés.
- Ne t'occupe pas des dates ni des heures : elles sont traitées ailleurs.

Exemples :
"on se fait un ciné à Bercy pour voir Interstellar"
-> activite cinema, zone "Bercy", chez null, avec [], film "Interstellar"
"je dîne chez Paul et Marie demain"
-> activite repas, zone null, chez "Paul et Marie", avec [], film null
"piscine avec Tom à Pontoise samedi matin"
-> activite sport, zone "Pontoise", chez null, avec ["Tom"], film null

Réponds uniquement avec le JSON demandé."""


def comprendre(phrase: str, modele: str = MODELE) -> Comprehension:
    import ollama  # importé ici : le reste du module reste utilisable sans Ollama (tests)

    reponse = ollama.chat(
        model=modele,
        messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": phrase}],
        format=Comprehension.model_json_schema(),
        options={"temperature": 0},
    )
    return Comprehension.model_validate_json(reponse.message.content)
