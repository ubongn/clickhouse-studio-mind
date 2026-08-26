-- ClickHouse Studio Mind — warehouse schema
-- Engine choices are deliberate:
--   * viewing_events: MergeTree partitioned by month (prunes 5/6 of the table for
--     single-month exec questions), ordered (title_id, episode_id, event_time) so
--     funnel/segment scans read contiguous ranges; bloom-filter skip index on
--     user_id for per-user slices; TTL keeps the demo warehouse self-cleaning.
--   * LowCardinality for low-plurality strings, Enum-free so LLM-generated SQL
--     never trips on enum values.
--   * Three materialized views maintain incrementally-updated aggregates
--     (AggregatingMergeTree with *State combinators) so exec queries read
--     pre-aggregated parts instead of rescanning 50M events.

CREATE DATABASE IF NOT EXISTS studio;

-- ---------------------------------------------------------------------------
-- Dimensions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS studio.titles
(
    title_id          UInt32,
    title_name        LowCardinality(String),
    title_type        LowCardinality(String),        -- series | film
    genre             LowCardinality(String),
    seasons           UInt8,
    total_episodes    UInt16,
    avg_ep_min        UInt8,
    cadence           LowCardinality(String),        -- weekly | binge
    quality_arc       LowCardinality(String),        -- editorial arc label (internal)
    popularity        Float32,
    is_original       Bool,
    release_dow       UInt8,                         -- 0=Mon .. 6=Sun
    ad_density        Float32                        -- ad load the title carries (0..1)
)
ENGINE = MergeTree
ORDER BY title_id;

CREATE TABLE IF NOT EXISTS studio.episodes
(
    episode_id    UInt32,                            -- title_id * 1000 + seq
    title_id      UInt32,
    season_no     UInt8,
    ep_number     UInt16,
    runtime_min   UInt16,
    quality_score Float32                            -- internal/editorial score 0..1
)
ENGINE = MergeTree
ORDER BY (title_id, episode_id);

CREATE TABLE IF NOT EXISTS studio.users
(
    user_id             UInt32,
    signup_date         Date,
    region              LowCardinality(String),      -- NA | EMEA | APAC | LATAM
    plan                LowCardinality(String),      -- basic_ads | standard | premium
    primary_device      LowCardinality(String),      -- tv | mobile | desktop | tablet
    acquisition_channel LowCardinality(String),      -- paid_social | search | partnership | organic | referral
    activity_per_30d    Float32,                     -- active days per 30 (propensity)
    churned             Bool,
    churn_date          Date,
    is_active           Bool,
    last_active_date    Date
)
ENGINE = MergeTree
ORDER BY (user_id);

-- ---------------------------------------------------------------------------
-- Fact: 50M viewing events
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS studio.viewing_events
(
    event_time       DateTime64(3) CODEC(Delta, ZSTD),
    event_date       Date MATERIALIZED toDate(event_time),
    user_id          UInt32,
    title_id         UInt32,
    episode_id       UInt32,
    season_no        UInt8,
    ep_number        UInt16,
    watched_seconds  UInt32,
    content_seconds  UInt32,
    completion_pct   Float32,                        -- 0..1
    completed        Bool,                           -- completion_pct >= 0.9
    ad_impressions   UInt8,
    ad_seconds       UInt16,
    rebuffer_count   UInt8,                          -- playback stalls this event
    rebuffer_seconds UInt16,                         -- total stall seconds
    device           LowCardinality(String),
    region           LowCardinality(String),
    plan             LowCardinality(String),
    is_binge         Bool,                           -- 3+ episodes of one title in a day
    session_pos      UInt8,                          -- 1-based position in that day's session
    INDEX idx_user user_id TYPE bloom_filter GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_time)
ORDER BY (title_id, episode_id, event_time)
TTL toDateTime(event_time) + INTERVAL 18 MONTH
SETTINGS index_granularity = 8192;

-- ---------------------------------------------------------------------------
-- Incremental aggregates (materialized views)
-- ---------------------------------------------------------------------------

-- Per-episode funnel facts: unique viewers, completion, watch time.
CREATE TABLE IF NOT EXISTS studio.episode_stats
(
    title_id        UInt32,
    episode_id      UInt32,
    season_no       UInt8,
    ep_number       UInt16,
    viewers         AggregateFunction(uniq, UInt32),
    avg_completion  AggregateFunction(avg, Float32),
    total_watched   AggregateFunction(sum, UInt32)
)
ENGINE = AggregatingMergeTree
ORDER BY (title_id, episode_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS studio.mv_episode_stats
TO studio.episode_stats AS
SELECT
    title_id,
    episode_id,
    season_no,
    ep_number,
    uniqState(user_id)    AS viewers,
    avgState(completion_pct) AS avg_completion,
    sumState(watched_seconds) AS total_watched
FROM studio.viewing_events
GROUP BY title_id, episode_id, season_no, ep_number;

-- Per-title daily engagement (drop-day spikes, decay curves).
CREATE TABLE IF NOT EXISTS studio.title_daily
(
    title_id      UInt32,
    day           Date,
    viewers       AggregateFunction(uniq, UInt32),
    events        AggregateFunction(count),
    total_watched AggregateFunction(sum, UInt32),
    avg_completion AggregateFunction(avg, Float32)
)
ENGINE = AggregatingMergeTree
ORDER BY (title_id, day);

CREATE MATERIALIZED VIEW IF NOT EXISTS studio.mv_title_daily
TO studio.title_daily AS
SELECT
    title_id,
    toDate(event_time) AS day,
    uniqState(user_id)     AS viewers,
    countState()           AS events,
    sumState(watched_seconds) AS total_watched,
    avgState(completion_pct)  AS avg_completion
FROM studio.viewing_events
GROUP BY title_id, day;

-- Daily active users by region and plan (cohort/retention cuts).
CREATE TABLE IF NOT EXISTS studio.region_plan_daily
(
    day     Date,
    region  LowCardinality(String),
    plan    LowCardinality(String),
    dau     AggregateFunction(uniq, UInt32),
    watched AggregateFunction(sum, UInt32)
)
ENGINE = AggregatingMergeTree
ORDER BY (day, region, plan);

CREATE MATERIALIZED VIEW IF NOT EXISTS studio.mv_region_plan_daily
TO studio.region_plan_daily AS
SELECT
    toDate(event_time) AS day,
    region,
    plan,
    uniqState(user_id)     AS dau,
    sumState(watched_seconds) AS watched
FROM studio.viewing_events
GROUP BY day, region, plan;
