"""Unit tests for the synthetic universe (fast: scaled-down universes)."""

from __future__ import annotations

import numpy as np

from data.universe import (
    FLAGSHIP_SLUG,
    GENRE_AFFINITY,
    GENRES,
    build_universe,
)


def test_same_seed_same_universe():
    a = build_universe(seed=7, n_titles=40, n_users=5_000, n_target_episodes=600)
    b = build_universe(seed=7, n_titles=40, n_users=5_000, n_target_episodes=600)
    assert list(a.title_id) == list(b.title_id)
    assert a.slug == b.slug
    assert np.array_equal(a.latent_quality, b.latent_quality)
    assert np.array_equal(a.user_country, b.user_country)


def test_different_seed_different_catalog():
    a = build_universe(seed=1, n_titles=40, n_users=2_000, n_target_episodes=500)
    b = build_universe(seed=2, n_titles=40, n_users=2_000, n_target_episodes=500)
    assert a.slug != b.slug


def test_flagship_is_nightfall_division_title_one():
    u = build_universe(seed=3, n_titles=30, n_users=2_000, n_target_episodes=400)
    assert u.slug[0] == FLAGSHIP_SLUG
    assert u.title_type[0] == "limited"
    assert int(u.is_flagship[0]) == 1
    assert u.is_flagship[1:].sum() == 0  # exactly one flagship


def test_nightfall_quality_cliff_at_episodes_4_and_5():
    u = build_universe(seed=3, n_titles=30, n_users=2_000, n_target_episodes=400)
    mask = (u.ep_title_id == u.title_id[0]) & (u.season == 1)
    q = u.latent_quality[mask]
    assert len(q) == 8, "flagship must be an 8-episode limited series"
    assert q[3] < 0.35 and q[4] < 0.35          # the cliff
    assert q[0] > 0.7 and q[2] > 0.6            # strong start
    assert q[7] > 0.7                            # finale recovers


def test_latent_quality_never_leaks_into_schema_columns():
    # the generator's whole point: quality shapes behavior, it is not stored
    import re

    from studio_mind import schema

    ddl = " ".join(schema.TABLE_STATEMENTS.values())
    assert not re.search(r"\bquality\b", ddl)  # playback_quality alone is fine


def test_episode_counts_and_types_make_sense():
    u = build_universe(seed=11, n_titles=60, n_users=2_000, n_target_episodes=900)
    assert u.n_episodes <= 900
    assert u.n_episodes > 300  # trimming shouldn't gut the catalog
    films = u.title_type == "film"
    assert films.sum() >= 5
    # every episode's title exists
    assert set(np.unique(u.ep_title_id)).issubset(set(u.title_id))


def test_users_have_realistic_mix():
    u = build_universe(seed=13, n_titles=10, n_users=20_000, n_target_episodes=150)
    plans = set(u.user_plan.tolist())
    assert plans == {"premium", "standard", "ad_supported"}
    acq = set(u.user_acquisition.tolist())
    assert acq == {"organic", "partner_bundle", "paid_social", "referral"}
    # ad_supported share around 25%
    share = (u.user_plan == "ad_supported").mean()
    assert 0.20 < share < 0.30


def test_regional_genre_affinity_rows_are_distributions():
    for country, weights in GENRE_AFFINITY.items():
        assert len(weights) == len(GENRES)
        assert abs(sum(weights) - 1.0) < 1e-6
        assert all(w >= 0 for w in weights)


def test_release_dates_are_weekly_within_a_season():
    u = build_universe(seed=17, n_titles=30, n_users=1_000, n_target_episodes=500)
    t0 = u.title_id[0]
    mask = (u.ep_title_id == t0) & (u.season == 1)
    rel = u.release_date[mask]
    gaps = np.diff(rel.astype("int64"))
    assert (gaps == 7).all()  # weekly drops for the flagship
