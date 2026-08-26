"""Catalog of the fictional streaming service "Nimbus+".

Deterministic: fixed RNG seed → identical catalog on every machine.
The catalog encodes *behavioral ground truth* (episode quality arcs, regional
affinity, cadence, ad density) that the generator turns into viewing events.
The agent's job is to rediscover these patterns from 50M events — never to
assume them.

Quality arcs (per episode, 0..1):
  strong        consistently excellent (0.72..0.9)
  declining     solid start, fades each season
  weak_middle   hyped opener, collapses mid-season, partial recovery  ← demo pattern
  slow_burn     rough start, word-of-mouth growth
  uneven        noisy around mid
  film          single point
"""

from __future__ import annotations

import numpy as np

SEED = 20260826

GENRES = (
    "drama", "thriller", "scifi", "comedy", "documentary", "reality",
    "animation", "crime", "horror", "romance", "fantasy",
)

REGIONS = ("NA", "EMEA", "APAC", "LATAM")

# Regional affinity multiplier per genre (drives completion & sampling weight).
# Real-world-plausible: crime/drama over-index in EMEA, animation in APAC,
# reality/comedy in NA, romance/drama in LATAM, etc.
REGION_GENRE_AFFINITY = {
    "NA":    {"drama": 1.00, "thriller": 1.05, "scifi": 1.05, "comedy": 1.15, "documentary": 1.05, "reality": 1.25, "animation": 0.95, "crime": 1.00, "horror": 1.10, "romance": 0.85, "fantasy": 1.05},
    "EMEA":  {"drama": 1.20, "thriller": 1.10, "scifi": 0.95, "comedy": 0.90, "documentary": 1.15, "reality": 0.85, "animation": 0.85, "crime": 1.30, "horror": 0.90, "romance": 0.90, "fantasy": 1.00},
    "APAC":  {"drama": 1.00, "thriller": 0.95, "scifi": 1.20, "comedy": 1.00, "documentary": 0.85, "reality": 1.00, "animation": 1.35, "crime": 0.95, "horror": 0.90, "romance": 1.05, "fantasy": 1.25},
    "LATAM": {"drama": 1.25, "thriller": 1.00, "scifi": 0.95, "comedy": 1.10, "documentary": 0.80, "reality": 1.15, "animation": 0.95, "crime": 1.05, "horror": 1.00, "romance": 1.30, "fantasy": 1.05},
}

# ---------------------------------------------------------------------------
# Flagship titles — hand-crafted, named, with narrative arcs.
#   (name, type, genre, seasons, eps/season, avg_min, cadence, arc,
#    popularity 0..1, original, release_day_of_week, ad_density 0..1)
# cadence: "weekly" = weekly episode drops; "binge" = all at once.
# ---------------------------------------------------------------------------

FLAGSHIPS = [
    # THE demo title: hyped scifi-thriller original whose mid-season collapse
    # is the signature question. Weekly drops, heavy ad load on ad tier.
    ("Nightfall Division",      "series", "thriller", 1, 8,  52, "weekly", "weak_middle", 0.98, True,  5, 0.85),
    ("The Cartographer's Wife", "series", "drama",    2, 8,  48, "weekly", "strong",      0.93, True,  0, 0.25),
    ("Iron Vestibule",          "series", "scifi",    2, 10, 46, "binge",  "slow_burn",   0.88, True,  0, 0.30),
    ("Grandmaster Nook",        "series", "animation",3, 12, 24, "binge",  "strong",      0.90, True,  0, 0.10),
    ("Salt & Circuit",          "series", "documentary",1, 6, 55, "weekly","strong",      0.72, True,  4, 0.20),
    ("Vermilion Hour",          "series", "crime",    3, 8,  54, "weekly", "strong",      0.91, True,  5, 0.40),
    ("Two Weeks in Lisbon",     "series", "romance",  1, 8,  44, "binge",  "uneven",      0.70, True,  0, 0.35),
    ("Dial Tone",               "series", "thriller", 2, 8,  50, "weekly", "declining",   0.86, True,  5, 0.55),
    ("Static Bloom",            "series", "horror",   1, 6,  47, "binge",  "uneven",      0.66, True,  0, 0.60),
    ("The Understudy",          "series", "comedy",   2, 10, 28, "binge",  "strong",      0.78, True,  0, 0.30),
    ("Porcelain Kings",         "series", "drama",    3, 10, 55, "weekly", "declining",   0.84, True,  0, 0.45),
    ("Rally Cap Nation",        "series", "reality",  4, 9,  42, "weekly", "uneven",      0.74, True,  4, 0.75),
    ("Nine Lanterns",           "series", "fantasy",  2, 8,  58, "weekly", "strong",      0.89, True,  5, 0.35),
    ("The Fetch Files",         "series", "comedy",   1, 8,  26, "binge",  "slow_burn",   0.62, True,  0, 0.40),
    ("Backcountry Wardens",     "series", "documentary",2, 8, 50, "weekly","strong",      0.68, False, 6, 0.50),
    ("Glass Amphitheater",      "series", "drama",    1, 8,  49, "binge",  "strong",      0.81, True,  0, 0.25),
    ("Cobalt Verdict",          "series", "crime",    2, 8,  53, "weekly", "declining",   0.80, False, 5, 0.55),
    ("Midnight Ramen Club",     "series", "animation",2, 12, 23, "binge",  "strong",      0.76, True,  0, 0.15),
    ("The Quiet Fathom",        "series", "thriller", 1, 6,  51, "weekly", "slow_burn",   0.71, True,  5, 0.40),
    ("Frontier Ledger",         "series", "documentary",1, 6, 52, "binge",  "uneven",      0.60, False, 0, 0.45),
    # Films (single "episode")
    ("The Last Aurora",         "film",   "scifi",    1, 1,  131, "binge", "film",        0.77, True,  0, 0.30),
    ("Harbor of Small Gods",    "film",   "drama",    1, 1,  118, "binge", "film",        0.73, True,  0, 0.25),
    ("Lemon Season",            "film",   "comedy",   1, 1,   96, "binge", "film",        0.64, False, 0, 0.55),
    ("Vellum",                  "film",   "horror",   1, 1,   88, "binge", "film",        0.61, True,  0, 0.65),
]

# Name parts for procedural catalog (avoid real-world IP).
_A = ("Copper", "Silent", "Broken", "Gilded", "Northern", "Crimson", "Paper", "Wild", "Hollow",
      "Second", "Winter", "Electric", "Saltwater", "Amber", "Midnight", "Cardboard", "Velvet",
      "Iron", "Cobalt", "Marble", "Glass", "Quiet", "Distant", "Feral", "Sunday", "Terminal",
      "Peach", "Static", "Lunar", "Rust", "Bright", "Frozen", "Guest", "Half", "Little")
_B = ("Orchard", "Protocol", "Requiem", "Circuit", "Vigil", "Parade", "Almanac", "Chorus",
      "Ledger", "Harvest", "Anthem", "Terminal", "Gospel", "Lullaby", "Monsoon", "Gazette",
      "Sonata", "Bazaar", "Foundry", "Atrium", "Compass", "Diary", "Reservoir", "Pavilion",
      "Archive", "Signal", "Carnival", "Warden", "Cipher", "Carousel", "Ferry", "Lantern")

_ARCS = ("strong", "declining", "weak_middle", "slow_burn", "uneven")


def quality_for_arc(arc: str, n_eps: int, rng: np.random.Generator) -> np.ndarray:
    """Per-episode quality scores in [0,1] following the named arc."""
    if arc == "film":
        return np.array([float(np.clip(rng.normal(0.68, 0.12), 0.3, 0.97))])
    if arc == "strong":
        base = rng.normal(0.80, 0.03)
        q = base + rng.normal(0, 0.035, n_eps)
    elif arc == "declining":
        start = rng.uniform(0.80, 0.88)
        slope = rng.uniform(-0.055, -0.030)
        q = start + slope * np.arange(n_eps) + rng.normal(0, 0.03, n_eps)
    elif arc == "weak_middle":
        # strong opener → collapse by ep4-5 → partial recovery
        start = rng.uniform(0.85, 0.92)
        floor = rng.uniform(0.34, 0.48)
        recover = rng.uniform(0.55, 0.72)
        x = np.arange(n_eps)
        collapse = np.clip(x / 4.0, 0, 1)
        base = start + (floor - start) * collapse
        rec_idx = (x >= 5) & (x <= 7)
        if rec_idx.any():
            rec_vals = np.linspace(floor, recover, int(rec_idx.sum()))
            base = base.copy()
            base[rec_idx] = np.maximum(base[rec_idx], rec_vals)
        q = base + rng.normal(0, 0.03, n_eps)
    elif arc == "slow_burn":
        start = rng.uniform(0.48, 0.58)
        slope = rng.uniform(0.045, 0.075)
        q = start + slope * np.arange(n_eps) + rng.normal(0, 0.03, n_eps)
    else:  # uneven
        q = rng.normal(0.62, 0.16, n_eps)
    return np.clip(q, 0.15, 0.97)


def build_catalog(seed: int = SEED) -> dict:
    """Return the full deterministic catalog: titles, episodes, arrays."""
    rng = np.random.default_rng(seed)

    titles = []
    # Flagships first (title_id 1..len) — deterministic ids make demos stable.
    for name, ttype, genre, seasons, eps_per, avg_min, cadence, arc, pop, original, dow, ad_d in FLAGSHIPS:
        titles.append(dict(
            name=name, type=ttype, genre=genre, seasons=seasons, eps_per_season=eps_per,
            avg_ep_min=int(avg_min), cadence=cadence, arc=arc, popularity=pop,
            original=original, release_dow=dow, ad_density=ad_d,
        ))

    # Procedural titles fill out the catalog.
    used_names = {t["name"] for t in titles}
    n_proc = 156
    i = 0
    while len(titles) < len(FLAGSHIPS) + n_proc:
        name = f"{_A[i % len(_A)]} {_B[(i * 7 + 3) % len(_B)]}"
        i += 1
        if name in used_names:
            continue
        used_names.add(name)
        ttype = "film" if rng.random() < 0.38 else "series"
        genre = GENRES[int(rng.integers(len(GENRES)))]
        if ttype == "film":
            titles.append(dict(
                name=name, type=ttype, genre=genre, seasons=1, eps_per_season=1,
                avg_ep_min=int(rng.integers(82, 148)), cadence="binge", arc="film",
                popularity=float(np.clip(rng.beta(2.2, 6.0) + 0.18, 0.05, 0.85)),
                original=bool(rng.random() < 0.42), release_dow=int(rng.integers(0, 7)),
                ad_density=float(np.clip(rng.beta(2.0, 3.2), 0.05, 0.95)),
            ))
        else:
            seasons = int(rng.integers(1, 4))
            eps = int(rng.integers(6, 11))
            titles.append(dict(
                name=name, type=ttype, genre=genre, seasons=seasons, eps_per_season=eps,
                avg_ep_min=int(rng.integers(24, 58)), cadence="weekly" if rng.random() < 0.45 else "binge",
                arc=_ARCS[int(rng.integers(len(_ARCS)))],
                popularity=float(np.clip(rng.beta(2.2, 6.0) + 0.18, 0.05, 0.85)),
                original=bool(rng.random() < 0.42), release_dow=int(rng.integers(0, 7)),
                ad_density=float(np.clip(rng.beta(2.0, 3.2), 0.05, 0.95)),
            ))

    # --- episodes ---------------------------------------------------------
    # episode_id = title_id * 1000 + seq  (fits comfortably in UInt32)
    ep_rows = []  # (episode_id, title_id, season, ep_number, runtime_min, quality)
    title_rows = []
    for tid, t in enumerate(titles, start=1):
        t["title_id"] = tid
        n_eps = t["seasons"] * t["eps_per_season"] if t["type"] == "series" else 1
        quals = quality_for_arc(t["arc"], n_eps, rng)
        if t["type"] == "series":
            for s in range(1, t["seasons"] + 1):
                for e in range(1, t["eps_per_season"] + 1):
                    seq = (s - 1) * t["eps_per_season"] + e
                    rt = int(np.clip(t["avg_ep_min"] + rng.normal(0, 2.5), 15, 95))
                    ep_rows.append((tid * 1000 + seq, tid, s, e, rt, round(float(quals[seq - 1]), 4)))
        else:
            rt = int(np.clip(t["avg_ep_min"] + rng.normal(0, 4), 60, 175))
            ep_rows.append((tid * 1000 + 1, tid, 1, 1, rt, round(float(quals[0]), 4)))

        title_rows.append((
            tid, t["name"], t["type"], t["genre"], t["seasons"],
            n_eps, t["avg_ep_min"], t["cadence"], t["arc"], t["popularity"],
            t["original"], t["release_dow"], t["ad_density"],
        ))

    return {
        "titles": title_rows,        # tuples for ClickHouse insert
        "episodes": ep_rows,
        "n_titles": len(titles),
        "n_episodes": len(ep_rows),
        "flagship_names": [t[1] for t in title_rows[: len(FLAGSHIPS)]],
    }


if __name__ == "__main__":
    cat = build_catalog()
    print(f"titles={cat['n_titles']} episodes={cat['n_episodes']}")
    nightfall = next(t for t in cat["titles"] if t[1] == "Nightfall Division")
    print("Nightfall Division:", nightfall)
    nf_eps = [e for e in cat["episodes"] if e[1] == nightfall[0]]
    print("episodes (id, tid, s, e, runtime, quality):")
    for e in nf_eps:
        print("  ", e)
