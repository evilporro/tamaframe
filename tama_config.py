# tama_config.py — Tamaframe balance settings
# Edit values here and redeploy to tune the game.
# Do not hardcode numbers in main.py.

# ============================================================
# COOLDOWNS & THRESHOLDS
# ============================================================
GLOBAL_COOLDOWN_MINUTES         = 5
ACTION_THRESHOLD                = 3
RELIC_CLICK_THRESHOLD           = 6
RELIC_REACTANTS_NEEDED          = 10

# ============================================================
# DECAY
# ============================================================
DECAY_INTERVAL                  = 120   # seconds per tick
DECAY_BASE                      = 4     # % lost per stat per tick
DECAY_SLEEP_FEED_MULT           = 0.3
DECAY_SLEEP_CLEAN_MULT          = 0.2
DECAY_SLEEP_REST_RECOVERY       = 0.5
DECAY_CORRUPTED_MULT            = 4
DECAY_TRIPLE_MULT               = 3
DECAY_TRIPLE_DURATION_MIN       = 15

# ============================================================
# REST NEGLECT MULTIPLIERS
# ============================================================
DECAY_REST_LOW_THRESHOLD        = 15
DECAY_REST_MID_THRESHOLD        = 30
DECAY_REST_LOW_MULT             = 2.0
DECAY_REST_MID_MULT             = 1.5

# ============================================================
# STAT GAINS & COSTS
# ============================================================
FEED_GAIN                       = 15
FEED_CLEAN_COST                 = 6
FEED_OVERFED_THRESHOLD          = 95
FEED_LOCK_DURATION_MIN          = 15

CLEAN_GAIN                      = 15
CLEAN_REST_COST                 = 4
CLEAN_OVER_THRESHOLD            = 95

TRAIN_STAT_GAIN                 = 15
TRAIN_FEED_COST                 = 8
TRAIN_CLEAN_COST                = 6
TRAIN_REST_COST                 = 10
TRAIN_OVERTRAIN_STREAK          = 3
TRAIN_BLOCK_REST_THRESHOLD      = 20

TRAIN_REST_TIER_LOW             = 25
TRAIN_REST_TIER_MID             = 50
TRAIN_REST_TIER_HIGH            = 75

TRAIN_REST_COST_HIGH            = 10
TRAIN_REST_COST_MID             = 25
TRAIN_REST_COST_LOW             = 35

TRAIN_REST_XP_MULT_FULL         = 1.0
TRAIN_REST_XP_MULT_HIGH         = 0.75
TRAIN_REST_XP_MULT_MID          = 0.5
TRAIN_REST_XP_MULT_LOW          = 0.25

TRAIN_EVO_XP                    = 100
FEED_EVO_XP                     = 20

# ============================================================
# XP & LEVELLING
# ============================================================
WARFRAME_XP_PER_LEVEL           = 180
PRIME_XP_PER_LEVEL              = 250
FORMA_XP_MULT_BONUS             = 0.5
FORMA_XP_MULT_MAX               = 3.0
AFFINITY_BOOST_XP               = 40

# ============================================================
# HAPPINESS THRESHOLDS
# ============================================================
HAPPINESS_THRIVING              = 80
HAPPINESS_CONTENT               = 60
HAPPINESS_STRUGGLING            = 40
HAPPINESS_SUFFERING             = 20

# ============================================================
# SLEEP
# ============================================================
SLEEP_DURATION_SECONDS          = 1800
SLEEP_MIN_BEFORE_WAKE           = 900
SLEEP_VOTE_TIMEOUT              = 300
SLEEP_YES_NEEDED                = 4
SLEEP_NO_NEEDED                 = 4
WAKE_YES_NEEDED                 = 4
WAKE_NO_NEEDED                  = 4
WAKE_EARLY_STAT_PENALTY         = 20

# ============================================================
# INFECTION & ROLLING GUARD
# ============================================================
SICK_THRESHOLD                  = 20
INFECTION_WARN_DURATION_SEC     = 300
INFECTION_IMMUNITY_MINUTES      = 15
ROLLING_GUARD_CLICKS_NEEDED     = 8
ROLLING_GUARD_WINDOW_MIN        = 20
ROLLING_GUARD_FAIL_STAT_HIT     = 15
ROLLING_GUARD_SUCCESS_BONUS     = 0

INFECTION_DECAY_PER_MIN         = 5
INFECTION_DECAY_CAP_MULT        = 3
INFECTION_PENALTY_DURATION_MIN  = 20
ACTION_THRESHOLD_POST_INFECTION = 4

# ============================================================
# DEATH
# ============================================================
DEATH_STAT_ZEROS_NEEDED         = 2
DEATH_STAT_ZERO_DURATION_SEC    = 1800

# ============================================================
# EVENTS
# ============================================================
EVENT_CATALYST_CLICKS           = 5
EVENT_FORMA_CLICKS              = 5
EVENT_AFFINITY_CLICKS           = 5
EVENT_CORRUPTED_CLICKS          = 5
EVENT_ROLLING_GUARD_CLICKS      = 5
EVENT_CORRUPTED_FAIL_HIT        = 10
EVENT_CATALYST_DURATION_MIN     = 30

EVENT_FISSURE_DURATION_MIN      = 15
EVENT_CATALYST_WINDOW_MIN       = 30
EVENT_FORMA_WINDOW_MIN          = 30
EVENT_AFFINITY_WINDOW_MIN       = 20
EVENT_CORRUPTED_GRACE_MIN       = 10
EVENT_CORRUPTED_WINDOW_MIN      = 20

# ============================================================
# PITY SYSTEM
# ============================================================
PITY_BASE_CHANCE                = 0.05
PITY_INCREMENT                  = 0.03
PITY_MIN_INTERVAL               = 60 * 60
EVENT_COOLDOWN                  = 30 * 60

# ============================================================
# ANTI-CHEESE & VARIANCE
# ============================================================
GAIN_VARIANCE                   = 0.30
NOISE_CHANCE                    = 0.40
NOISE_REST_THRESHOLD            = 30
NOISE_CLEAN_THRESHOLD           = 20
FEED_BACKFIRE_CLEAN_COST        = 5
OVERFEED_XP_MULT                = 0.5
IMMUNITY_GAIN_MULT              = 0.75
CONSECUTIVE_SAME_PENALTY        = 2

# ============================================================
# CONSTANTS
# ============================================================
STAT_MAX                        = 100
LEVEL_MAX                       = 30
