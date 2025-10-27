from .db import execute

SCHEMA_SQL = '''
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    tg_id BIGINT UNIQUE NOT NULL,
    language TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'inactive', -- inactive | active
    referrer_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    balance_qc BIGINT NOT NULL DEFAULT 0,
    earned_total_qc BIGINT NOT NULL DEFAULT 0,
    today_date DATE DEFAULT NULL,
    today_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    uuid TEXT UNIQUE NOT NULL,
    amount_usd NUMERIC(10,2) NOT NULL,
    status TEXT NOT NULL DEFAULT 'created', -- created|paid|partial|overpaid|canceled
    link TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chains (
    id BIGSERIAL PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS steps (
    id BIGSERIAL PRIMARY KEY,
    chain_id BIGINT REFERENCES chains(id) ON DELETE CASCADE,
    order_no INT NOT NULL,
    title_uk TEXT,
    title_ru TEXT,
    title_en TEXT,
    desc_uk TEXT NOT NULL,
    desc_ru TEXT NOT NULL,
    desc_en TEXT NOT NULL,
    url TEXT NOT NULL,
    reward_qc INT NOT NULL,
    verify_chat_id BIGINT, -- if set, we check membership in this chat
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(chain_id, order_no)
);

CREATE TABLE IF NOT EXISTS user_steps (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    step_id BIGINT REFERENCES steps(id) ON DELETE CASCADE,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, step_id)
);

CREATE TABLE IF NOT EXISTS user_chain_state (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    chain_id BIGINT REFERENCES chains(id) ON DELETE CASCADE,
    next_available_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, chain_id)
);

CREATE TABLE IF NOT EXISTS withdrawals (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    amount_qc BIGINT NOT NULL,
    country TEXT,
    method TEXT,
    details TEXT,
    status TEXT NOT NULL DEFAULT 'pending', -- pending|processed|paid
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS referral_rewards (
    id BIGSERIAL PRIMARY KEY,
    referrer_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    referee_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    awarded BOOLEAN NOT NULL DEFAULT FALSE,
    awarded_at TIMESTAMPTZ,
    UNIQUE(referee_id)
);
'''
async def run_stars_migration():
    # 1) колонки
    await execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS provider TEXT")
    await execute("UPDATE payments SET provider='cryptocloud' WHERE provider IS NULL")
    await execute("ALTER TABLE payments ALTER COLUMN provider SET DEFAULT 'cryptocloud'")
    await execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_name='payments' AND column_name='provider'
                   AND is_nullable='NO'
            ) THEN
                ALTER TABLE payments ALTER COLUMN provider SET NOT NULL;
            END IF;
        END$$;
    """)

    await execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS order_id TEXT")
    await execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS currency TEXT")
    await execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS amount_stars INTEGER")

    -- створюємо саме CONSTRAINT (не індекс), щоб ON CONFLICT гарантовано спрацював
    await execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'payments_provider_order_uniq'
            ) THEN
                ALTER TABLE payments
                ADD CONSTRAINT payments_provider_order_uniq
                UNIQUE (provider, order_id);
            END IF;
        END$$;
    """)

    -- (не обовʼязково) допоміжний індекс по provider
    await execute("""
        CREATE INDEX IF NOT EXISTS payments_provider_idx
        ON payments(provider)
    """)

async def ensure_schema():
    await execute(SCHEMA_SQL)
