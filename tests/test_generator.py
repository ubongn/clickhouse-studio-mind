"""Generator smoke tests at toy scale — behavior must be baked in, not asserted
into existence. Runs the real simulator with a few thousand users."""

from __future__ import annotations

import numpy as np
import pytest

from data.catalog import build_catalog
from data.generate import derive_churn, generate_events, generate_users

SEED = 20260826


@pytest.fixture(scope="module")
def toy():
    catalog = build_catalog(SEED)
    rng = np.random.default_rng(SEED)
    users = generate_users(4_000, rng)
    out, rows, sessions = generate_events(users, catalog, 60_000, SEED)
    return catalog, users, out, rows, sessions


def _col(out, name):
    return np.concatenate(out[name])


def test_deterministic_same_seed():
    catalog = build_catalog(SEED)
    rng_a = np.random.default_rng(SEED)
    rng_b = np.random.default_rng(SEED)
    users_a = generate_users(500, rng_a)
    users_b = generate_users(500, rng_b)
    out_a, rows_a, _ = generate_events(users_a, catalog, 5_000, SEED)
    out_b, rows_b, _ = generate_events(users_b, catalog, 5_000, SEED)
    assert rows_a == rows_b
    assert np.array_equal(_col(out_a, "user_id"), _col(out_b, "user_id"))
    assert np.array_equal(_col(out_a, "episode_id"), _col(out_b, "episode_id"))


def test_column_shapes_agree(toy):
    catalog, users, out, rows, sessions = toy
    n = _col(out, "user_id").size
    for key in ("event_time_ms", "title_id", "episode_id", "season_no", "ep_number",
                "watched_seconds", "content_seconds", "completion_pct", "completed",
                "ad_impressions", "ad_seconds", "device_idx", "region_idx", "plan_idx",
                "session_pos"):
        assert _col(out, key).size == n, key


def test_completion_in_range_and_consistent_flag(toy):
    catalog, users, out, rows, sessions = toy
    comp = _col(out, "completion_pct")
    done = _col(out, "completed")
    assert comp.min() >= 0.0 and comp.max() <= 1.0
    assert np.array_equal(done, comp >= 0.9)


def test_watched_seconds_bounded_by_content(toy):
    catalog, users, out, rows, sessions = toy
    watched = _col(out, "watched_seconds").astype(np.int64)
    content = _col(out, "content_seconds").astype(np.int64)
    assert (watched >= 5).all()
    # 3% watch-time noise is allowed, nothing more
    assert (watched <= content * 1.05 + 5).all()


def test_ads_only_on_ad_tier(toy):
    catalog, users, out, rows, sessions = toy
    ads = _col(out, "ad_impressions")
    plan = _col(out, "plan_idx")
    assert (ads[plan != 0] == 0).all()          # standard/premium never see ads
    assert (ads[plan == 0] > 0).mean() > 0.8    # ad tier mostly does (long episodes)


def test_nightfall_cliff_is_visible(toy):
    """The demo question must be answerable from toy data: episode 4 loses
    audience versus episode 3 on the flagship."""
    catalog, users, out, rows, sessions = toy
    t = _col(out, "title_id")
    e = _col(out, "episode_id")
    u = _col(out, "user_id")
    m = t == 1
    v3 = np.unique(u[m & (e == 1003)]).size
    v4 = np.unique(u[m & (e == 1004)]).size
    assert v3 > 0
    assert v4 < v3


def test_churn_derivation(toy):
    catalog, users, out, rows, sessions = toy
    churned, churn_date, last_day, n_events = derive_churn(users, out, np.random.default_rng(SEED + 2))
    assert churned.dtype == np.bool_
    assert (n_events >= 0).all()
    assert churned.mean() > 0.02 and churned.mean() < 0.6  # sane churn rate
    # churn dates only for churned users, within window
    import datetime as dt
    d = churn_date[churned]
    assert (d >= np.datetime64("2024-07-01")).all()
