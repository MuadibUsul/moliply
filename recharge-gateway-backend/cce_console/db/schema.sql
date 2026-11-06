PRAGMA journal_mode=WAL;
PRAGMA foreign_keys = ON;

-- Risk-isolation unit. Accounts inside one batch share a card BIN, billing
-- template and mailbox domain; batches never mix, so a ban wave is contained.
CREATE TABLE IF NOT EXISTS payment_batches (
    batch_id TEXT PRIMARY KEY,
    bin_prefix TEXT NOT NULL,
    billing_country TEXT NOT NULL,
    max_accounts INTEGER DEFAULT 10,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- One row == one sellable Claude identity. The fingerprint and the egress IP
-- are decided exactly once, at creation, and never regenerated.
CREATE TABLE IF NOT EXISTS profiles (
    profile_id TEXT PRIMARY KEY,
    batch_id TEXT,
    local_proxy_port INTEGER UNIQUE NOT NULL,
    upstream_proxy_url TEXT NOT NULL,
    expected_ip TEXT NOT NULL,
    os_type TEXT NOT NULL CHECK(os_type IN ('macos', 'windows', 'linux')),
    noise_seed INTEGER NOT NULL,
    fingerprint_json TEXT NOT NULL,
    geo_json TEXT,
    claude_credentials_enc BLOB,
    status TEXT DEFAULT 'IDLE' CHECK(status IN ('IDLE', 'BUSY', 'COOLDOWN', 'AUTH_EXPIRED', 'PROXY_DEAD', 'BANNED')),
    active_session_id TEXT,
    -- Lease clock: set when a session is claimed and refreshed on every
    -- heartbeat. A BUSY row that stops beating has lost its client and is
    -- reclaimed by the server sweep.
    last_heartbeat_at TIMESTAMP,
    cooldown_until TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(batch_id) REFERENCES payment_batches(batch_id)
);

CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles(status);
CREATE INDEX IF NOT EXISTS idx_profiles_batch ON profiles(batch_id);

-- Customer-facing delivery credential. The card key is what the launcher
-- presents; the tunnel secret is the second factor for the relay gateway.
CREATE TABLE IF NOT EXISTS delivery_keys (
    card_key TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    plan_tier TEXT NOT NULL CHECK(plan_tier IN ('pro', 'max_5x', 'max_20x', 'env_only')),
    tunnel_secret TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY(profile_id) REFERENCES profiles(profile_id)
);

CREATE INDEX IF NOT EXISTS idx_delivery_keys_profile ON delivery_keys(profile_id);

-- Cards used to buy subscriptions. PAN and CVV are sealed with CCE_MASTER_KEY.
-- 'in_use' means one attempt has earmarked the card; a hard decline buries it
-- as 'dead' so the same number is never retried into a card-network fuse.
CREATE TABLE IF NOT EXISTS subscription_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_bin TEXT NOT NULL,
    last4 TEXT NOT NULL,
    pan_enc BLOB NOT NULL,
    cvv_enc BLOB NOT NULL,
    exp_month TEXT NOT NULL,
    exp_year TEXT NOT NULL,
    billing_zip TEXT DEFAULT '',
    holder TEXT DEFAULT '',
    balance_usd REAL DEFAULT 0,
    status TEXT DEFAULT 'available'
        CHECK(status IN ('available', 'in_use', 'used', 'dead')),
    last_error TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cards_status ON subscription_cards(status);

-- Account material and per-profile pipeline state. Kept beside the profile so a
-- profile can exist before an account does, and the delivery path never has to
-- know how the account was obtained.
CREATE TABLE IF NOT EXISTS account_jobs (
    profile_id TEXT PRIMARY KEY,
    email TEXT NOT NULL DEFAULT '',
    password_enc BLOB,
    gmail_app_password_enc BLOB,
    plan TEXT NOT NULL DEFAULT 'pro',
    stage TEXT NOT NULL DEFAULT 'queued'
        CHECK(stage IN ('queued', 'registering', 'registered', 'subscribing', 'subscribed',
                        'needs_human', 'failed', 'ready')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    human_note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(profile_id) REFERENCES profiles(profile_id)
);

CREATE INDEX IF NOT EXISTS idx_account_jobs_stage ON account_jobs(stage);
