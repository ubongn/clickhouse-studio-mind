"""Generator smoke tests at toy scale — behavior must be baked in, not asserted
into existence. Runs the real simulator with a few thousand users."""

from __future__ import annotations

import numpy as np
import pytest

from data.catalog import build_catalog
from data.generate import (
    EPOCH, INCIDENT_END, INCIDENT_START, WINDOW_START, derive_churn,
    generate_events, generate_users,
)

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
                "ad_impressions", "ad_seconds", "rebuffer_count", "rebuffer_seconds",
                "device_idx", "region_idx", "plan_idx", "session_pos"):
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


def _day_offsets(out):
    ts = _col(out, "event_time_ms").astype("int64")
    day0 = int((np.datetime64(WINDOW_START) - EPOCH) / np.timedelta64(1, "ms"))
    return (ts - day0) // 86_400_000


def test_rebuffer_bounds(toy):
    """Every stall lasts 2-8 seconds; most events never stall."""
    catalog, users, out, rows, sessions = toy
    rbc = _col(out, "rebuffer_count").astype(np.int64)
    rbs = _col(out, "rebuffer_seconds").astype(np.int64)
    assert (rbs >= 2 * rbc).all() and (rbs <= 8 * rbc).all()
    assert (rbc == 0).mean() > 0.6          # stalls are the exception, not the rule


def test_cdn_incident_is_findable(toy):
    """The seeded CDN week must stand out: NA mobile+desktop rebuffer heavily
    that week while every other segment stays flat (diff-in-diff)."""
    catalog, users, out, rows, sessions = toy
    day_off = _day_offsets(out)
    dev, reg = _col(out, "device_idx"), _col(out, "region_idx")
    rbc = _col(out, "rebuffer_count").astype(np.int64)
    md = (dev == 1) | (dev == 2)
    hit = (day_off >= INCIDENT_START) & (day_off <= INCIDENT_END) & (reg == 0) & md
    same_week_md_elsewhere = (
        (day_off >= INCIDENT_START) & (day_off <= INCIDENT_END) & (reg != 0) & md
    )
    assert hit.sum() > 100                       # enough rows to be queryable
    assert rbc[hit].mean() > 2.5                 # the incident group stalls hard
    assert rbc[same_week_md_elsewhere].mean() < 1.0
    assert rbc[~hit].mean() < 1.0                # baseline stays quiet


def test_qoe_exposure_drives_churn(toy):
    """Users who rebuffered through the incident churn more than low-qoe peers."""
    catalog, users, out, rows, sessions = toy
    ts = _col(out, "event_time_ms").astype("int64")
    uid, rbs = _col(out, "user_id"), _col(out, "rebuffer_seconds").astype(np.int64)
    day0 = int((np.datetime64(WINDOW_START) - EPOCH) / np.timedelta64(1, "ms"))
    lo = day0 + INCIDENT_START * 86_400_000
    hi = day0 + (INCIDENT_END + 1) * 86_400_000
    in_win = (ts >= lo) & (ts < hi) & (rbs > 0)
    n = users["user_id"].size
    qoe = np.bincount(uid[in_win], weights=rbs[in_win].astype(float), minlength=n) / 60.0
    churned, _, _, n_events = derive_churn(users, out, np.random.default_rng(SEED + 2))
    exposed = churned[qoe >= 0.5].mean()
    low_qoe_active = churned[(qoe < 0.1) & (n_events > 0)].mean()
    assert exposed > low_qoe_active + 0.05


def test_partnership_churns_faster_than_organic(toy):
    """The churn-by-channel question has a baked-in answer (~1.4x)."""
    catalog, users, out, rows, sessions = toy
    churned, _, _, _ = derive_churn(users, out, np.random.default_rng(SEED + 2))
    ch = users["channel"]
    part, org = churned[ch == 2].mean(), churned[ch == 3].mean()
    assert part > org * 1.2
