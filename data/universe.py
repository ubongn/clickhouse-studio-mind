"""The synthetic streaming universe behind Studio Mind.

Deterministic (seeded) generation of the dimension tables:

- ~180 titles across 10 genres — series, limited series and films —
  including one flagship original, **Nightfall Division**, whose season
  one is engineered to collapse at episode 4.
- ~4,500 episodes with weekly release cadence and per-episode latent
  quality (the thing the DIAGNOSE stage is supposed to *discover*, so
  it is never stored as a column — it only shapes behavior).
- 1.2M users with plan tiers, acquisition channels and regional genre
  affinity that the simulator will react to.

Everything is plain numpy so the generator has no heavy deps and the
same seed always yields the same universe.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------- catalog ---

GENRES = (
    "thriller", "drama", "comedy", "documentary", "scifi",
    "romance", "crime", "animation", "horror", "reality",
)

COUNTRIES = (
    "US", "IN", "BR", "GB", "DE", "NG", "JP", "KR", "FR", "MX", "ID", "OTHER",
)
COUNTRY_WEIGHTS = np.array([0.28, 0.12, 0.08, 0.06, 0.05, 0.04, 0.04, 0.03, 0.03, 0.03, 0.02, 0.22])

# Regional genre affinity — rows sum to 1 per country (index-aligned with GENRES).
GENRE_AFFINITY: dict[str, np.ndarray] = {
    "US": [0.16, 0.12, 0.12, 0.08, 0.10, 0.07, 0.12, 0.07, 0.08, 0.08],
    "IN": [0.10, 0.22, 0.14, 0.04, 0.06, 0.20, 0.06, 0.10, 0.03, 0.05],
    "BR": [0.12, 0.18, 0.10, 0.05, 0.07, 0.16, 0.09, 0.06, 0.05, 0.12],
    "GB": [0.18, 0.13, 0.11, 0.10, 0.09, 0.06, 0.13, 0.06, 0.07, 0.07],
    "DE": [0.17, 0.14, 0.10, 0.12, 0.09, 0.06, 0.13, 0.06, 0.08, 0.05],
    "NG": [0.14, 0.20, 0.13, 0.05, 0.06, 0.14, 0.09, 0.07, 0.05, 0.07],
    "JP": [0.09, 0.12, 0.11, 0.07, 0.10, 0.08, 0.07, 0.22, 0.09, 0.05],
    "KR": [0.13, 0.17, 0.10, 0.06, 0.08, 0.13, 0.09, 0.11, 0.07, 0.06],
    "FR": [0.12, 0.16, 0.11, 0.11, 0.09, 0.11, 0.10, 0.08, 0.07, 0.05],
    "MX": [0.11, 0.17, 0.11, 0.05, 0.07, 0.16, 0.09, 0.07, 0.06, 0.11],
    "ID": [0.11, 0.18, 0.11, 0.05, 0.07, 0.15, 0.08, 0.09, 0.07, 0.09],
    "OTHER": [0.13, 0.15, 0.11, 0.09, 0.08, 0.10, 0.11, 0.08, 0.08, 0.07],
}

_TITLE_PREFIX = (
    "Crimson", "Silent", "Broken", "Neon", "Velvet", "Iron", "Paper", "Glass",
    "Midnight", "Golden", "Savage", "Hollow", "Electric", "Distant", "Wild",
    "Last", "First", "Final", "Scarlet", "Quiet",
)
_TITLE_NOUN = (
    "Harbor", "Protocol", "Garden", "Circuit", "Crown", "Verdict", "Symphony",
    "Highway", "Bureau", "Legacy", "Orchard", "Signal", "Empire", "Requiem",
    "Lantern", "Frontier", "Archive", "Parade", "Atlas", "Bazaar",
)
_SUFFIXES = ("", "", "", "", " II", " III", ": Origins", ": Aftermath", " Nights")

FLAGSHIP_SLUG = "nightfall-division"
FLAGSHIP_TITLE = "Nightfall Division"


@dataclass(frozen=True)
class Universe:
    """Dimension rows as columnar numpy arrays."""

    title_id: np.ndarray          # UInt32
    slug: list[str]
    title_type: np.ndarray        # S7 bytes ('series'|'limited'|'film')
    genre: np.ndarray             # S12 bytes
    is_original: np.ndarray       # UInt8
    is_flagship: np.ndarray       # UInt8
    premiere_date: np.ndarray     # datetime64[D]
    origin_country: np.ndarray    # S2 bytes
    ad_load_sec_per_hr: np.ndarray  # UInt16

    episode_id: np.ndarray        # UInt32
    ep_title_id: np.ndarray       # UInt32 (fk)
    season: np.ndarray            # UInt8
    ep_number: np.ndarray         # UInt8
    release_date: np.ndarray      # datetime64[D]
    runtime_min: np.ndarray       # UInt16
    latent_quality: np.ndarray    # float32 in [0,1] — behavior-only, never loaded

    user_id: np.ndarray           # UInt32
    user_country: np.ndarray      # S5 bytes
    user_plan: np.ndarray         # S12 bytes
    user_acquisition: np.ndarray  # S14 bytes
    user_signup: np.ndarray       # datetime64[D]

    @property
    def n_titles(self) -> int:
        return len(self.title_id)

    @property
    def n_episodes(self) -> int:
        return len(self.episode_id)

    @property
    def n_users(self) -> int:
        return len(self.user_id)


def _slugify(name: str) -> str:
    return name.lower().replace(" ", "-").replace(":", "")


def build_titles(rng: np.random.Generator, n_titles: int = 180):
    """Generate the catalog. Nightfall Division is always title_id 1."""
    n_other = n_titles - 1
    types = rng.choice(("series", "limited", "film"), size=n_other, p=(0.62, 0.18, 0.20))
    genres = rng.choice(GENRES, size=n_other)

    names: list[str] = [FLAGSHIP_TITLE]
    used = {FLAGSHIP_TITLE}
    while len(names) < n_titles:
        name = f"{rng.choice(_TITLE_PREFIX)} {rng.choice(_TITLE_NOUN)}{rng.choice(_SUFFIXES)}"
        if name not in used:
            used.add(name)
            names.append(name)

    title_type = np.array(["limited"] + list(types))
    genre = np.array([rng.choice(("thriller", "scifi", "crime"))] + list(genres))
    is_original = (rng.random(n_titles) < 0.55).astype(np.uint8)
    is_flagship = np.zeros(n_titles, dtype=np.uint8)
    is_flagship[0] = 1  # Nightfall Division
    is_original[0] = 1

    premiere = np.array(
        np.datetime64("2025-07-01")
        + (rng.integers(0, 300, n_titles)).astype("timedelta64[D]")
    )
    origin = rng.choice(("US", "GB", "CA", "JP", "KR", "IN", "FR", "DE", "NG", "BR"), size=n_titles)
    # ad load: only some titles carry mid-roll inventory (seconds per hour)
    ad_load = np.where(rng.random(n_titles) < 0.6, rng.integers(120, 301, n_titles), 0).astype(np.uint16)

    title_id = np.arange(1, n_titles + 1, dtype=np.uint32)
    slug = [_slugify(n) for n in names]
    return title_id, slug, title_type, genre, is_original, is_flagship, premiere, origin, ad_load


def _episode_plan(rng: np.random.Generator, title_type: str, quality_base: float, flagship: bool):
    """Return (seasons, eps_per_season, weekly_gap_days) for a title."""
    if flagship:
        return [(1, 8)], 7  # Nightfall Division: 8-episode limited series
    if title_type == "film":
        return [(1, 1)], 0
    if title_type == "limited":
        return [(1, int(rng.integers(4, 9)))], 7
    n_seasons = int(rng.integers(1, 4))
    return [(s, int(rng.integers(6, 14))) for s in range(1, n_seasons + 1)], 7


def build_episodes(rng: np.random.Generator, titles, n_target_episodes: int = 4_500):
    (title_id, _slug, title_type, _genre, _is_orig, is_flagship, premiere, _origin, _ad) = titles

    ep_title: list[int] = []
    season: list[int] = []
    number: list[int] = []
    release: list[np.datetime64] = []
    runtime: list[int] = []
    quality: list[float] = []

    for i in range(len(title_id)):
        ttype = title_type[i]
        base_q = float(np.clip(rng.beta(9, 3), 0.45, 0.95))  # most episodes decent
        plans, gap = _episode_plan(rng, ttype, base_q, bool(is_flagship[i]))
        base_runtime = 48 if ttype == "series" else 96 if ttype == "limited" else 108
        cursor = np.datetime64(premiere[i])
        for s, n_eps in plans:
            for e in range(1, n_eps + 1):
                ep_title.append(int(title_id[i]))
                season.append(s)
                number.append(e)
                release.append(cursor)
                runtime.append(int(np.clip(rng.normal(base_runtime, 8), 22, 180)))
                if ttype == "film":
                    quality.append(base_q)
                else:
                    quality.append(float(np.clip(rng.normal(base_q, 0.10), 0.05, 0.99)))
                cursor = cursor + np.timedelta64(gap, "D")
                # long gaps between seasons
                if e == n_eps:
                    cursor = cursor + np.timedelta64(int(rng.integers(90, 240)), "D")

    # Nightfall Division quality cliff: episodes 4 and 5 of S1 collapse.
    t0 = int(title_id[0])
    idx = [j for j, (t, s, e) in enumerate(zip(ep_title, season, number)) if t == t0 and s == 1]
    for e, q in ((4, 0.28), (5, 0.31)):
        if e <= len(idx):
            quality[idx[e - 1]] = q
    # and a strong pilot + finale so the funnel shape reads as a mid-season problem
    if len(idx) >= 8:
        quality[idx[0]] = 0.82
        quality[idx[7]] = 0.78

    # trim/pad to target episode count by dropping tail of the longest series
    order = np.argsort(quality)  # stable-ish; we simply truncate extras
    if len(quality) > n_target_episodes:
        keep = np.ones(len(quality), dtype=bool)
        # drop lowest-information films until at target
        film_idx = [j for j in range(len(quality)) if ep_title[j] in
                    [int(t) for t, tt in zip(title_id, title_type) if tt == "film"]]
        for j in film_idx[::-1]:
            if keep.sum() <= n_target_episodes:
                break
            keep[j] = False
        ep_title = [t for t, k in zip(ep_title, keep) if k]
        season = [s for s, k in zip(season, keep) if k]
        number = [n for n, k in zip(number, keep) if k]
        release = [r for r, k in zip(release, keep) if k]
        runtime = [r for r, k in zip(runtime, keep) if k]
        quality = [q for q, k in zip(quality, keep) if k]

    episode_id = np.arange(1, len(ep_title) + 1, dtype=np.uint32)
    return (
        episode_id,
        np.array(ep_title, dtype=np.uint32),
        np.array(season, dtype=np.uint8),
        np.array(number, dtype=np.uint8),
        np.array(release, dtype="datetime64[D]"),
        np.array(runtime, dtype=np.uint16),
        np.array(quality, dtype=np.float32),
    )


def build_users(rng: np.random.Generator, n_users: int = 1_200_000):
    user_id = np.arange(1, n_users + 1, dtype=np.uint32)
    country = rng.choice(COUNTRIES, size=n_users, p=COUNTRY_WEIGHTS / COUNTRY_WEIGHTS.sum())
    plan = rng.choice(("premium", "standard", "ad_supported"), size=n_users, p=(0.30, 0.45, 0.25))
    acquisition = rng.choice(
        ("organic", "partner_bundle", "paid_social", "referral"), size=n_users, p=(0.55, 0.20, 0.15, 0.10)
    )
    signup = np.array(
        np.datetime64("2024-01-01")
        + rng.integers(0, 365, n_users).astype("timedelta64[D]")
    )
    return user_id, country, plan, acquisition, signup


def build_universe(seed: int = 20260826, n_titles: int = 180,
                   n_users: int = 1_200_000, n_target_episodes: int = 4_500) -> Universe:
    rng = np.random.default_rng(seed)
    titles = build_titles(rng, n_titles)
    episodes = build_episodes(rng, titles, n_target_episodes)
    users = build_users(rng, n_users)
    return Universe(
        title_id=titles[0], slug=titles[1], title_type=titles[2], genre=titles[3],
        is_original=titles[4], is_flagship=titles[5], premiere_date=titles[6],
        origin_country=titles[7], ad_load_sec_per_hr=titles[8],
        episode_id=episodes[0], ep_title_id=episodes[1], season=episodes[2],
        ep_number=episodes[3], release_date=episodes[4], runtime_min=episodes[5],
        latent_quality=episodes[6],
        user_id=users[0], user_country=users[1], user_plan=users[2],
        user_acquisition=users[3], user_signup=users[4],
    )
