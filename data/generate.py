"""Deterministic 50M-event viewing dataset for Nimbus+ (fictional streamer).

Behavioral realism baked in (all discoverable by the agent, none hardcoded):
  * episode quality arcs drive completion → audience collapse on weak arcs
  * weekly drop cadence → premiere-day spikes on the title's release weekday
  * binge-model titles → longer sessions, higher in-session survival
  * ad-supported tier carries ad-impression friction (completion penalty scaled
    by each title's ad density)
  * regional genre affinity (EMEA/crime, APAC/animation, LATAM/romance, ...)
  * device effects (TV completes more, mobile less)
  * QoE telemetry: playback stalls per event (device-weighted Poisson), stalls
    trim completion; plus a seeded CDN incident week (late May, NA,
    mobile+desktop ~10x stalls) that completion and churn queries must find
  * evening/weekend viewing rhythm
  * churn: inactivity + disengagement (low completion) + plan/channel hazards
    + QoE exits (heavy incident-week rebuffering)

Everything is a pure function of --seed. Run:

    python -m data.generate                     # 50M rows (default from .env)
    python -m data.generate --rows 2000000      # quick smoke dataset
"""

from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np

try:  # works without python-dotenv when env vars are already set
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.catalog import REGION_GENRE_AFFINITY, build_catalog  # noqa: E402

# --- window -----------------------------------------------------------------
WINDOW_START = date(2026, 2, 1)
WINDOW_DAYS = 181                      # 2026-02-01 .. 2026-07-31
WINDOW_END = WINDOW_START + timedelta(days=WINDOW_DAYS - 1)

REGIONS = ("NA", "EMEA", "APAC", "LATAM")
PLANS = ("basic_ads", "standard", "premium")
DEVICES = ("tv", "mobile", "desktop", "tablet")
CHANNELS = ("paid_social", "search", "partnership", "organic", "referral")

REGION_P = np.array([0.38, 0.27, 0.22, 0.13])
PLAN_P = np.array([0.45, 0.35, 0.20])
DEVICE_P = np.array([0.34, 0.36, 0.22, 0.08])
DEVICE_REBUFFER = np.array([0.5, 1.6, 1.0, 0.8])   # rebuffer propensity, idx into DEVICES
# CDN incident: one bad week in late May, NA only, mobile+desktop hit hardest.
# The anomaly morning-brief / QoE→churn queries must find it.
INCIDENT_START, INCIDENT_END = 108, 115             # day offsets from WINDOW_START
CHANNEL_P = np.array([0.30, 0.18, 0.14, 0.28, 0.10])
DEVICE_COMPLETION = {"tv": 1.05, "tablet": 1.00, "desktop": 0.97, "mobile": 0.90}
DEVICE_CONTINUE = {"tv": 1.045, "tablet": 1.0, "desktop": 0.99, "mobile": 0.955}

EPOCH = np.datetime64("1970-01-01T00:00:00.000")


# ============================================================================
# dimensions
# ============================================================================

def generate_users(n_users: int, rng: np.random.Generator):
    """Users sorted by signup day (enables day-bounded sampling via cdf cutoff)."""
    # Cohort months 2024-07 .. 2026-07 (25 months). 85% pre-window, 15% in-window.
    months = [date(2024, 7, 1) + timedelta(days=31 * i) for i in range(25)]
    w = np.array([0.35 * (i + 1) for i in range(25)])
    w[:12] *= 0.55                      # older cohorts smaller
    w = w / w.sum()
    month_idx = rng.choice(len(months), size=n_users, p=w)
    day_off = rng.integers(0, 28, size=n_users)
    signup = np.array(
        [np.datetime64(months[m] + timedelta(days=int(d))) for m, d in zip(month_idx, day_off)]
    )

    region_idx = rng.choice(len(REGIONS), size=n_users, p=REGION_P)
    plan_idx = rng.choice(len(PLANS), size=n_users, p=PLAN_P)
    device_idx = rng.choice(len(DEVICES), size=n_users, p=DEVICE_P)
    channel_idx = rng.choice(len(CHANNELS), size=n_users, p=CHANNEL_P)

    activity = np.clip(rng.gamma(2.3, 1 / 2.1, size=n_users), 0.4, 26.0)  # active days / 30d
    # heavier viewers skew premium/tv
    activity *= 1.0 + 0.18 * (plan_idx == 2) + 0.08 * (device_idx == 0)

    order = np.argsort(signup, kind="stable")
    return dict(
        user_id=(np.arange(1, n_users + 1, dtype=np.uint32)),
        signup=signup[order],
        region=region_idx[order],
        plan=plan_idx[order],
        device=device_idx[order],
        channel=channel_idx[order],
        activity=activity[order].astype(np.float32),
    )


def air_dates(catalog: dict, rng: np.random.Generator):
    """First-aired day offset (from WINDOW_START) for each title; per-episode offsets."""
    n_titles = catalog["n_titles"]
    first_day = np.zeros(n_titles + 1, dtype=np.int32)         # 1-based title ids
    for row in catalog["titles"]:
        tid, name, ttype, genre, seasons, n_eps, avg_min, cadence = row[:8]
        if name == "Nightfall Division":
            d0 = 28                                              # demo anchor: Sat 2026-03-07
        else:
            d0 = int(rng.integers(8, 118)) if cadence == "weekly" else int(rng.integers(0, 148))
        first_day[tid] = d0
    # per-episode air offsets: weekly = d0 + 7*(seq-1); binge = d0
    ep_air = {}
    for ep in catalog["episodes"]:
        eid, tid, s, e, rt, q = ep
        seq = eid - tid * 1000
        cadence = catalog["titles"][tid - 1][7]
        ep_air[eid] = first_day[tid] + (7 * (seq - 1) if cadence == "weekly" else 0)
    return first_day, ep_air


# ============================================================================
# event generation
# ============================================================================

def generate_events(users: dict, catalog: dict, target_rows: int, seed: int, log=print):
    rng = np.random.default_rng(seed + 1)
    first_day, ep_air = air_dates(catalog, rng)

    n_titles = catalog["n_titles"]
    titles = catalog["titles"]                    # tuples
    episodes = catalog["episodes"]                # tuples

    # --- flattened episode lookup arrays -----------------------------------
    ep_title = np.array([e[1] for e in episodes], dtype=np.uint32)
    ep_seq = np.array([e[0] - e[1] * 1000 for e in episodes], dtype=np.uint16)
    ep_season = np.array([e[2] for e in episodes], dtype=np.uint8)
    ep_number = np.array([e[3] for e in episodes], dtype=np.uint16)
    ep_runtime = np.array([e[4] for e in episodes], dtype=np.uint16)
    ep_quality = np.array([e[5] for e in episodes], dtype=np.float32)
    ep_air_off = np.array([ep_air[e[0]] for e in episodes], dtype=np.int32)
    # index: (title_id, seq) → row in episodes arrays
    max_seq = int(ep_seq.max())
    ep_index = np.full((n_titles + 1, max_seq + 2), -1, dtype=np.int32)   # +1 col: over-run → -1
    for i, (eid, tid) in enumerate(zip([e[0] for e in episodes], ep_title)):
        ep_index[tid, eid - tid * 1000] = i

    title_pop = np.zeros(n_titles + 1, dtype=np.float64)
    title_genre = np.empty(n_titles + 1, dtype=object)
    title_ad = np.zeros(n_titles + 1, dtype=np.float64)
    title_cadence = np.empty(n_titles + 1, dtype=object)
    for row in titles:
        tid, name, ttype, genre, seasons, n_eps, avg_min, cadence = row[:8]
        pop, orig, dow, ad_d = row[9], row[10], row[11], row[12]
        title_pop[tid] = pop
        title_genre[tid] = genre
        title_ad[tid] = ad_d
        title_cadence[tid] = cadence

    # title sampling weights per region (popularity × regional affinity)
    title_w = np.zeros((len(REGIONS), n_titles + 1))
    for r, region in enumerate(REGIONS):
        aff = REGION_GENRE_AFFINITY[region]
        for tid in range(1, n_titles + 1):
            title_w[r, tid] = title_pop[tid] * aff.get(title_genre[tid], 1.0)
    title_w[:, 0] = 0
    title_cdf = np.cumsum(title_w, axis=1)
    title_cdf /= title_cdf[:, -1:]

    # users prefix-cdf for day-bounded weighted sampling
    w = users["activity"].astype(np.float64)
    wcdf = np.cumsum(w)

    hour_p = np.array([1.3, 0.9, 0.55, 0.35, 0.3, 0.4, 0.6, 0.9, 1.1, 1.2, 1.25, 1.3,
                       1.35, 1.3, 1.35, 1.5, 1.8, 2.3, 3.2, 3.6, 3.4, 2.9, 2.3, 1.8])
    hour_p /= hour_p.sum()
    hour_cdf = np.cumsum(hour_p)

    dow_factor = np.array([0.92, 0.90, 0.93, 0.97, 1.06, 1.30, 1.22])   # Mon..Sun

    # output buffers
    out = {c: [] for c in (
        "event_time_ms", "user_id", "title_id", "episode_id", "season_no", "ep_number",
        "watched_seconds", "content_seconds", "completion_pct", "completed",
        "ad_impressions", "ad_seconds", "rebuffer_count", "rebuffer_seconds",
        "device_idx", "region_idx", "plan_idx", "is_binge", "session_pos",
    )}
    total_rows = 0
    total_sessions = 0
    events_per_session = 1.72
    day0_ms = int((np.datetime64(WINDOW_START) - EPOCH) / np.timedelta64(1, "ms"))

    for d in range(WINDOW_DAYS):
        remaining_days = WINDOW_DAYS - d
        # adaptive rate so we land on target regardless of realized session length
        need = max(0, target_rows - total_rows)
        rows_this = need / max(1, remaining_days)
        n_sessions = int(rows_this / max(1.2, events_per_session))

        dow = (WINDOW_START + timedelta(days=d)).weekday()
        factor = dow_factor[dow] * (1.0 + 0.12 * d / WINDOW_DAYS)       # mild growth
        n_sessions = int(n_sessions * factor)
        if n_sessions <= 0:
            continue

        # sample users alive on day d (signup ≤ d)
        cutoff = np.searchsorted(users["signup"], np.datetime64(WINDOW_START + timedelta(days=d)), side="right")
        if cutoff < 100:
            continue
        u_cdf = wcdf[:cutoff]
        u = rng.random(n_sessions) * u_cdf[-1]
        uidx = np.searchsorted(u_cdf, u)
        np.clip(uidx, 0, cutoff - 1, out=uidx)

        sess_user = uidx.astype(np.int64)
        sess_region = users["region"][sess_user]
        sess_plan = users["plan"][sess_user]
        sess_device = users["device"][sess_user]

        # device per session (12% drift off primary device)
        drift = rng.random(n_sessions) < 0.12
        drift_dev = rng.choice(len(DEVICES), size=n_sessions, p=DEVICE_P)
        sess_device = np.where(drift, drift_dev, sess_device)

        # title by regional affinity
        tu = rng.random(n_sessions)
        sess_title = np.empty(n_sessions, dtype=np.int64)
        for r in range(len(REGIONS)):
            m = sess_region == r
            if m.any():
                sess_title[m] = np.searchsorted(title_cdf[r], tu[m])

        # start position: geometric over aired episodes, premieres overweighted
        t_first = first_day[sess_title]
        is_weekly = np.array([title_cadence[t] == "weekly" for t in sess_title])
        max_aired_seq = np.where(
            is_weekly,
            np.minimum((d - t_first) // 7 + 1, 36),
            np.where(d >= t_first, 36, 0),
        ).astype(np.int64)
        fresh = (d - t_first) <= 20
        max_aired_seq = np.where(fresh, 1, max_aired_seq)          # new shows: start at ep1
        valid = (d >= t_first) & (max_aired_seq >= 1)
        keep = np.where(valid)[0]
        if keep.size == 0:
            continue

        sess_user, sess_title = sess_user[keep], sess_title[keep]
        sess_device, sess_plan, sess_region = sess_device[keep], sess_plan[keep], sess_region[keep]
        max_seq_s = max_aired_seq[keep]
        is_binge_model = ~is_weekly[keep]

        # geometric(0.85) start position for back-catalog, clipped
        g = np.floor(np.log(rng.random(sess_title.size) + 1e-12) / np.log(0.85)) + 1
        start_seq = np.where(
            rng.random(sess_title.size) < 0.45,
            1,
            np.clip(g, 1, max_seq_s),
        ).astype(np.int64)

        # session start time
        sh = np.searchsorted(hour_cdf, rng.random(sess_title.size))
        sm = rng.integers(0, 60, size=sess_title.size)
        ss = rng.integers(0, 60, size=sess_title.size)
        day_ms = int((np.datetime64(WINDOW_START + timedelta(days=d)) - EPOCH) / np.timedelta64(1, "ms"))
        ts_ms = day_ms + (sh * 3600 + sm * 60 + ss) * 1000

        alive = np.ones(sess_title.size, dtype=bool)
        cur_seq = start_seq.copy()
        cur_ts = ts_ms.astype(np.int64)
        sess_len = np.zeros(sess_title.size, dtype=np.int32)
        total_sessions += sess_title.size

        wave_rows = 0
        while alive.any():
            idx = np.where(alive)[0]
            erow = ep_index[sess_title[idx], cur_seq[idx]]
            ok = erow >= 0
            if not ok.all():                       # ran past available episodes
                alive[idx[~ok]] = False
                idx = idx[ok]
                if idx.size == 0:
                    break
                erow = erow[ok].astype(np.int64)
            else:
                erow = erow.astype(np.int64)

            t = sess_title[idx]
            sq = cur_seq[idx]
            dev = sess_device[idx]
            plan = sess_plan[idx]
            tid_arr = t

            q = ep_quality[erow]
            runtime = ep_runtime[erow].astype(np.int64)
            dev = sess_device[idx]
            plan = sess_plan[idx]
            tid_arr = t

            # completion model
            mean = 0.28 + 0.66 * q
            dev_f = np.take(np.array([DEVICE_COMPLETION[d] for d in DEVICES]), dev)
            ad_den = title_ad[tid_arr]
            is_ads = plan == 0
            mean = mean * dev_f * np.where(is_ads, 1 - 0.12 * ad_den, 1.0)

            # QoE: playback stalls (Poisson, device-weighted); each stall trims
            # expected completion up to -5.5% (friction compounds). During the
            # CDN incident week NA mobile+desktop rebuffer ~10x baseline.
            day_off = (cur_ts[idx].astype("int64") - day0_ms) // 86_400_000
            incident = (
                (day_off >= INCIDENT_START) & (day_off <= INCIDENT_END)
                & (sess_region[idx] == 0) & ((dev == 1) | (dev == 2))
            )
            rb_prop = DEVICE_REBUFFER[dev]
            rebuf = rng.poisson(0.22 * rb_prop + np.where(incident, 2.6 * rb_prop, 0.0))
            rebuf_s = rebuf * rng.integers(2, 9, size=rebuf.size)
            mean = mean * (1.0 - 0.055 * np.minimum(rebuf, 4) / 4.0)

            mean = np.clip(mean, 0.10, 0.97)
            kappa = 13.0
            comp = rng.beta(mean * kappa, (1 - mean) * kappa)
            comp = np.clip(comp, 0.02, 1.0)

            content_s = runtime * 60
            watch_noise = rng.uniform(0.97, 1.03, size=comp.size)
            watched = np.maximum(5, (content_s * comp * watch_noise).astype(np.int64))

            ads = np.zeros(comp.size, dtype=np.int64)
            m_ads = is_ads & (runtime > 12)
            ads[m_ads] = np.maximum(
                1, (runtime[m_ads] / 9.0 * (0.6 + 0.9 * ad_den[m_ads])
                    * rng.uniform(0.85, 1.15, size=int(m_ads.sum()))).round()
            )
            ad_secs = (ads * rng.integers(20, 35, size=ads.size)).astype(np.int64)

            sess_len[idx] += 1
            wave_rows += comp.size

            out["event_time_ms"].append(cur_ts[idx])
            out["user_id"].append(users["user_id"][sess_user[idx]])
            out["title_id"].append(tid_arr.astype(np.uint32))
            out["episode_id"].append((tid_arr * 1000 + sq).astype(np.uint32))
            out["season_no"].append(ep_season[erow])
            out["ep_number"].append(ep_number[erow])
            out["watched_seconds"].append(watched.astype(np.uint32))
            out["content_seconds"].append(content_s.astype(np.uint32))
            out["completion_pct"].append(comp.astype(np.float32))
            out["completed"].append((comp >= 0.9))
            out["ad_impressions"].append(ads.astype(np.uint8))
            out["ad_seconds"].append(ad_secs.astype(np.uint16))
            out["rebuffer_count"].append(rebuf.astype(np.uint8))
            out["rebuffer_seconds"].append(
                np.minimum(rebuf_s, 65_535).astype(np.uint16))
            out["device_idx"].append(dev)
            out["region_idx"].append(sess_region[idx])
            out["plan_idx"].append(plan)
            out["is_binge"].append(np.zeros(comp.size, dtype=bool))     # fixed post-hoc
            out["session_pos"].append(sess_len[idx].astype(np.uint8))

            # in-session survival
            dev_c = np.take(np.array([DEVICE_CONTINUE[d] for d in DEVICES]), dev)
            p_cont = 0.58 + 0.36 * comp - 0.018 * np.minimum(sess_len[idx], 4)
            p_cont = np.clip(p_cont, 0.08, 0.95) * dev_c
            p_cont = np.clip(p_cont * np.where(is_binge_model[idx], 1.06, 1.0)
                             * np.where(plan == 2, 1.03, 1.0), 0.05, 0.96)
            cont = rng.random(comp.size) < p_cont

            # advance: time += watched + gap; seq += 1
            gap_ms = (rng.lognormal(np.log(4.2 * 60_000), 0.9, size=comp.size)).astype(np.int64)
            cur_ts[idx] = cur_ts[idx] + watched * 1000 + np.where(is_binge_model[idx], gap_ms // 3, gap_ms)
            cur_seq[idx] = np.minimum(cur_seq[idx] + 1, max_seq + 1)
            alive[idx] = cont

        total_rows += wave_rows
        if total_sessions > 0:
            events_per_session = 0.9 * events_per_session + 0.1 * (total_rows / total_sessions)

        if (d + 1) % 20 == 0:
            log(f"  day {d+1}/{WINDOW_DAYS}: rows={total_rows:,} sessions={total_sessions:,} eps={events_per_session:.2f}")

    out["session_len_full"] = None
    return out, total_rows, total_sessions


def derive_churn(users: dict, out: dict, rng: np.random.Generator):
    """Post-process: per-user last active day + churn state from behavior."""
    uid = np.concatenate(out["user_id"]) if len(out["user_id"]) else np.array([], dtype=np.uint32)
    ts = np.concatenate(out["event_time_ms"]) if len(out["event_time_ms"]) else np.array([], dtype=np.int64)
    comp = np.concatenate(out["completion_pct"]) if len(out["completion_pct"]) else np.array([], dtype=np.float32)
    rbs = (np.concatenate(out["rebuffer_seconds"])
           if len(out["rebuffer_seconds"]) else np.array([], dtype=np.uint16))

    n = users["user_id"].size
    order = np.argsort(ts)
    uid_s, ts_s, comp_s = uid[order], ts[order], comp[order]

    # per-user aggregation via reduceat on sorted-by-(user, time): first sort by user then ts
    order2 = np.lexsort((ts, uid))
    uid_s, ts_s, comp_s = uid[order2], ts[order2], comp[order2]
    rbs_s = rbs[order2] if rbs.size else rbs       # aligned with uid_s/ts_s

    starts = np.searchsorted(uid_s, np.arange(1, n + 1), side="left")
    ends = np.searchsorted(uid_s, np.arange(1, n + 1), side="right")
    has_events = ends > starts

    last_day = np.full(n, np.datetime64("1970-01-01"), dtype="datetime64[D]")
    n_events = np.zeros(n, dtype=np.int64)
    mean_comp = np.full(n, 0.45, dtype=np.float64)
    last_day[has_events] = (ts_s[ends[has_events] - 1].astype("datetime64[ms]").astype("datetime64[D]"))
    n_events[has_events] = ends[has_events] - starts[has_events]
    csum = np.concatenate([[0.0], np.cumsum(comp_s, dtype=np.float64)])
    mean_comp[has_events] = (csum[ends[has_events]] - csum[starts[has_events]]) / n_events[has_events]

    # QoE exposure: rebuffer minutes suffered during the CDN incident week
    # (uid_s/ts_s/rbs_s are the same lexsort order — bincount pairs correctly)
    incident_lo = int(
        (np.datetime64(WINDOW_START + timedelta(days=INCIDENT_START)) - EPOCH)
        / np.timedelta64(1, "ms"))
    incident_hi = int(
        (np.datetime64(WINDOW_START + timedelta(days=INCIDENT_END + 1)) - EPOCH)
        / np.timedelta64(1, "ms"))
    qoe = np.zeros(n, dtype=np.float64)
    if rbs_s.size:
        in_win = (ts_s >= incident_lo) & (ts_s < incident_hi) & (rbs_s > 0)
        qoe_sums = np.bincount(uid_s[in_win], weights=rbs_s[in_win].astype(np.float64),
                               minlength=n)
        qoe = qoe_sums / 60.0                       # incident rebuffer minutes per user

    plan, channel = users["plan"], users["channel"]
    plan_hazard = np.array([1.15, 0.85, 0.60])[plan]
    chan_hazard = np.array([1.20, 1.00, 1.38, 0.82, 0.90])[channel]

    inactive = last_day < (np.datetime64(WINDOW_END) - np.timedelta64(21, "D"))
    disengaged = (mean_comp < 0.40) & (n_events >= 6)
    churned = inactive.copy()
    # disengaged-but-still-around quiet quits
    quiet = (~inactive) & disengaged & (rng.random(n) < 0.38)
    churned = churned | quiet
    # QoE-driven churn: heavy rebuffering during the CDN incident → quiet-exit
    # risk (up to 45% for the worst-hit users)
    qoe_quit = (~inactive) & (qoe >= 0.5) & (rng.random(n) < np.minimum(qoe / 6.0, 0.45))
    churned = churned | qoe_quit

    churn_offset = np.minimum(rng.geometric(0.35, size=n), 60).astype(np.int32)
    churn_date = np.where(
        churned,
        np.clip(last_day.astype("datetime64[D]") + churn_offset,
                users["signup"], np.datetime64(WINDOW_END)),
        np.datetime64("1970-01-01"),
    )
    return churned, churn_date.astype("datetime64[D]"), last_day.astype("datetime64[D]"), n_events


# ============================================================================
# main
# ============================================================================

def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the Nimbus+ viewing dataset")
    ap.add_argument("--rows", type=int, default=int(os.getenv("GENERATOR_ROWS", 50_000_000)))
    ap.add_argument("--users", type=int, default=1_200_000)
    ap.add_argument("--seed", type=int, default=int(os.getenv("GENERATOR_SEED", 20260826)))
    ap.add_argument("--database", default=os.getenv("CLICKHOUSE_DATABASE", "studio"))
    ap.add_argument("--dry-run", action="store_true", help="generate + print stats, no ClickHouse")
    args = ap.parse_args()

    t0 = time.time()
    print(f"[catalog] seed={args.seed}")
    catalog = build_catalog(args.seed)
    print(f"[catalog] {catalog['n_titles']} titles, {catalog['n_episodes']} episodes")

    rng = np.random.default_rng(args.seed)
    users = generate_users(args.users, rng)
    print(f"[users]   {args.users:,} users  ({time.time()-t0:.1f}s)")

    print(f"[events]  target={args.rows:,} rows over {WINDOW_DAYS} days …")
    out, total_rows, total_sessions = generate_events(users, catalog, args.rows, args.seed)
    print(f"[events]  generated {total_rows:,} rows in {total_sessions:,} sessions ({time.time()-t0:.1f}s)")

    churned, churn_date, last_day, n_events = derive_churn(users, out, np.random.default_rng(args.seed + 2))
    print(f"[churn]   churned={churned.sum():,} ({churned.mean():.1%})  active={np.sum(n_events>0):,} users w/ events")

    if args.dry_run:
        # quick self-check: Nightfall funnel from generated arrays
        t_all = np.concatenate(out["title_id"]); u_all = np.concatenate(out["user_id"])
        e_all = np.concatenate(out["episode_id"]); c_all = np.concatenate(out["completion_pct"])
        m = t_all == 1
        for eid in (1001, 1002, 1003, 1004, 1005, 1006):
            mm = m & (e_all == eid)
            if mm.any():
                print(f"  Nightfall ep{eid-1000}: viewers={np.unique(u_all[mm]).size:,} avg_completion={c_all[mm].mean():.2f}")
        print(f"[dry-run] total={total_rows:,} rows — OK")
        return

    import clickhouse_connect
    from data.load import load_all  # noqa: E402

    client = clickhouse_connect.get_client(
        url=os.getenv("CLICKHOUSE_URL", "http://localhost:8123"),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        database=args.database,
    )
    load_all(client, catalog, users, out, churned, churn_date, last_day, database=args.database)
    print(f"[done]    {time.time()-t0:.1f}s total")


if __name__ == "__main__":
    main()
