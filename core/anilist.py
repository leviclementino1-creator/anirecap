"""Cliente AniList (GraphQL público) pra buscar nomes canônicos de personagens.

AniList é um banco aberto de animes/manga com nomes oficiais dos personagens
(romaji + variações). Usado pra normalizar nomes da legenda (que vem em
localizações imprecisas tipo "Kieffrey" em FR) pro canônico ("Qifrey").

API:
- Endpoint: https://graphql.anilist.co
- Free, sem auth, rate limit ~90 req/min
- Schema: https://anilist.gitbook.io/anilist-apiv2-docs/

Cache em disco por título do anime — busca uma vez, persiste pra sempre.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

import requests


ANILIST_ENDPOINT = "https://graphql.anilist.co"


@dataclass
class CharacterInfo:
    """Info de um personagem canônico vindo do AniList."""
    full: str               # nome completo (canônico, ex: "Qifrey")
    role: str = ""          # MAIN, SUPPORTING, BACKGROUND
    alternatives: List[str] = field(default_factory=list)  # outras grafias

    def all_names(self) -> List[str]:
        """Lista de todas as grafias conhecidas (canônico + alternativos)."""
        out = [self.full] if self.full else []
        out.extend(self.alternatives or [])
        return [n.strip() for n in out if n and n.strip()]


# Query GraphQL: busca anime + até 50 personagens com alternativas
_QUERY = """\
query ($search: String) {
  Media(search: $search, type: ANIME, sort: [SEARCH_MATCH]) {
    id
    title { romaji english native }
    characters(perPage: 50, sort: [ROLE, FAVOURITES_DESC]) {
      edges {
        role
        node {
          name {
            full
            alternative
          }
        }
      }
    }
  }
}
"""


def fetch_anime_characters(
    title: str,
    timeout: float = 15.0,
) -> Optional[List[CharacterInfo]]:
    """Busca personagens canônicos de um anime no AniList.

    Retorna None se a busca falhar (anime não encontrado, rede off, etc).
    Retorna lista vazia se anime existe mas sem personagens cadastrados.

    A primeira batida em SEARCH_MATCH costuma ser o anime certo. Se o
    título tem variantes (S01, S2, season 2), AniList tipicamente retorna
    a S1 — os personagens principais são os mesmos.
    """
    if not title or not title.strip():
        return None

    try:
        resp = requests.post(
            ANILIST_ENDPOINT,
            json={"query": _QUERY, "variables": {"search": title.strip()}},
            timeout=timeout,
        )
    except (requests.RequestException, OSError):
        return None

    if resp.status_code != 200:
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    media = (data or {}).get("data", {}).get("Media")
    if not media:
        return None

    chars: List[CharacterInfo] = []
    for edge in (media.get("characters") or {}).get("edges") or []:
        node = edge.get("node") or {}
        name = node.get("name") or {}
        full = (name.get("full") or "").strip()
        if not full:
            continue
        alts = [a for a in (name.get("alternative") or []) if a]
        chars.append(CharacterInfo(
            full=full,
            role=edge.get("role") or "",
            alternatives=alts,
        ))
    return chars


# Tags comuns em filenames de release (.mkv) — removidas pra extrair só
# o título do anime. Lista MUITO conservadora: tudo que vem depois de
# "S\d+E\d+" ou de tag tipo `[GROUP]` é considerado metadata da release.
_RELEASE_TAGS_RE = re.compile(
    r"\b("
    r"S\d{1,2}E\d{1,3}"           # S01E04
    r"|S\d{1,2}"                  # S01
    r"|E\d{1,3}"                  # E04
    r"|\d{3,4}p"                  # 1080p, 720p
    r"|MULTi|DUAL|JAP|JPN|ENG|FRE"
    r"|x26[45]|H\.?26[45]|AVC|HEVC"
    r"|AAC[0-9.]*|AC3|DTS|FLAC|EAC3"
    r"|WEB-?DL|WEBRip|BluRay|BDRip|DVDRip|HDTV"
    r"|CR|FUNi|NF|AMZN|DSNP|HULU"
    r"|MSubs|Multi-Subs|Dual-Audio"
    r"|AD"                        # Audio Description tag
    r")\b",
    flags=re.IGNORECASE,
)
_BRACKET_TAGS_RE = re.compile(r"[\[\(].*?[\]\)]")
_REPEATED_SEPARATORS_RE = re.compile(r"[\s._-]+")


def extract_title_from_filename(filename: str) -> str:
    """Extrai o título do anime do nome do arquivo .mkv.

    Heurística:
    1. Tira extensão.
    2. Remove tags entre `[]` ou `()` (group, language, quality).
    3. Remove conhecidos termos de release (codecs, source, season/episode).
    4. Normaliza separadores (`.` `_` `-` viram espaço único).
    5. Trim.

    Exemplos:
        'Witch Hat Atelier S01E04 MULTi AD 1080p CR WEB-DL AAC2.0 x264-Tsundere-Raws.mkv'
            → 'Witch Hat Atelier'
        '[ToonsHub] Witch Hat Atelier S01E04 1080p CR WEB-DL DUAL AAC2.0 H.264.mkv'
            → 'Witch Hat Atelier'
        'Dr STONE S04E28 MULTi AD 1080p CR WEB-DL AAC2.0 x264-Tsundere-Raws.mkv'
            → 'Dr STONE'
    """
    if not filename:
        return ""

    # 1) Sem extensão
    name = re.sub(r"\.(mkv|mp4|m4v|avi|webm)$", "", filename, flags=re.IGNORECASE)

    # 2) Remove tags entre parênteses/colchetes
    name = _BRACKET_TAGS_RE.sub("", name)

    # 3) Remove tags de release. Achamos a primeira ocorrência e cortamos
    # tudo ali em diante — o título sempre vem ANTES.
    m = _RELEASE_TAGS_RE.search(name)
    if m:
        name = name[:m.start()]

    # 4) Normaliza separadores
    name = _REPEATED_SEPARATORS_RE.sub(" ", name)

    # 5) Trim
    return name.strip()
