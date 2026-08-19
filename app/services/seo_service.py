"""
Reserved for the in-app SEO assistant (keyword/tag suggestions for
restaurant listings), backed by the self-hosted Ollama instance.

NOT implemented yet - on hold pending confirmation of which model is
actually installed on the Ollama server. Building this against a guessed
model name/capability would likely need rework anyway, so the interface
is defined here and left unimplemented rather than guessed at.

Per the Build Brief: this stays scoped to keyword/tag/category
suggestions only. It is not a general content generator and should not
draft descriptions, menu copy, or marketing text on a restaurant's behalf.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeoSuggestionRequest:
    restaurant_name: str
    cuisine: str
    suburb: str
    city: str
    current_description: str | None = None


@dataclass(frozen=True)
class SeoSuggestion:
    suggested_keywords: list[str]
    suggested_tags: list[str]


def suggest_keywords_and_tags(request: SeoSuggestionRequest) -> SeoSuggestion:
    """
    Intended flow: prompt the self-hosted Ollama model with the
    restaurant's cuisine/location/description and ask for relevant
    search keywords and listing tags, scoped narrowly (no free-form
    content generation).

    Left unimplemented until the specific installed Ollama model is
    confirmed - model choice affects prompt structure and expected
    output format enough that guessing now would likely mean rewriting
    this once the real model is known.
    """
    raise NotImplementedError(
        "SEO assistant is on hold pending confirmation of the installed Ollama model. "
        "See app/services/seo_service.py docstring."
    )
