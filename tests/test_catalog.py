"""Catalog invariants — the fictional universe must be stable and sane."""

from __future__ import annotations

import numpy as np

from data.catalog import FLAGSHIPS, build_catalog, quality_for_arc


def test_catalog_is_deterministic():
    a = build_catalog(20260826)
    b = build_catalog(20260826)
    assert a["n_titles"] == b["n_titles"]
    assert a["titles"] == b["titles"]
    assert a["episodes"] == b["episodes"]


def test_catalog_shape():
    cat = build_catalog(20260826)
    assert cat["n_titles"] == 180
    assert 1500 < cat["n_episodes"] < 2600
    assert "Nightfall Division" in cat["flagship_names"]


def test_flagships_present_and_first():
    catalog = build_catalog(20260826)
    names = [row[1] for row in catalog["titles"]]
    # Nightfall Division is title_id 1 — the demo question's subject.
    assert catalog["titles"][0][1] == "Nightfall Division"
    for row in FLAGSHIPS:
        assert row[0] in names


def test_nightfall_arc_collapses():
    """The demo title must have a real mid-season collapse for the funnel story."""
    cat = build_catalog(20260826)
    nf = next(t for t in cat["titles"] if t[1] == "Nightfall Division")
    quals = [e[5] for e in cat["episodes"] if e[1] == nf[0]]
    assert len(quals) == 8
    assert quals[0] > 0.80                       # strong premiere
    assert min(quals[3:5]) < 0.55                # collapse by ep4/5
    assert quals[-1] > min(quals[3:5])           # partial recovery at the end


def test_quality_arc_bounds():
    rng = np.random.default_rng(7)
    for arc in ("strong", "declining", "weak_middle", "slow_burn", "uneven"):
        q = quality_for_arc(arc, 10, rng)
        assert q.shape == (10,)
        assert q.min() >= 0.15 and q.max() <= 0.97


def test_episode_ids_encode_title_and_sequence():
    catalog = build_catalog(20260826)
    eps_per = {row[0]: row[5] for row in catalog["titles"]}   # total eps per title
    for eid, tid, season, ep, runtime, quality in catalog["episodes"][:80]:
        seq = eid - tid * 1000          # global sequence within the title
        assert 1 <= seq <= eps_per[tid]
        assert 1 <= season
        assert 1 <= ep
        assert runtime > 0
        assert 0.0 <= quality <= 1.0


def test_episode_counts_match_title_rows():
    catalog = build_catalog(20260826)
    per_title = {}
    for eid, tid, *_ in catalog["episodes"]:
        per_title[tid] = per_title.get(tid, 0) + 1
    for row in catalog["titles"]:
        tid, name, ttype, genre, seasons, n_eps_total = row[:6]
        assert per_title.get(tid, 0) == n_eps_total, (name, per_title.get(tid), n_eps_total)


def test_ad_density_within_bounds():
    catalog = build_catalog(20260826)
    for row in catalog["titles"]:
        ad = row[12]  # (name,type,genre,seasons,eps,min,cadence,arc,pop,orig,dow,ad_density)
        assert 0.0 <= ad <= 1.0


def test_regional_affinity_positive_everywhere():
    from data.catalog import GENRES, REGION_GENRE_AFFINITY

    for region, table in REGION_GENRE_AFFINITY.items():
        assert set(table) == set(GENRES)
        assert all(v > 0 for v in table.values())


def test_latent_quality_has_spread():
    q = np.array([ep[5] for ep in build_catalog(20260826)["episodes"]])
    assert q.min() < 0.45 and q.max() > 0.8  # room for weak and strong arcs
