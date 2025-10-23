# migrations/versions/20251023_initial.py
from alembic import op
import sqlalchemy as sa

revision = "20251023_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tg_id", sa.BigInteger, nullable=False),
        sa.Column("lang", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="inactive"),
        sa.Column("referrer_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_users_tg_id", "users", ["tg_id"], unique=True)
    op.create_index("ix_users_status", "users", ["status"])

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("amount_usd", sa.Float, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_payments_uuid", "payments", ["uuid"], unique=True)
    op.create_index("ix_payments_status", "payments", ["status"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title_uk", sa.String(), nullable=False),
        sa.Column("title_ru", sa.String(), nullable=False),
        sa.Column("title_en", sa.String(), nullable=False),
        sa.Column("desc_uk", sa.Text(), nullable=False),
        sa.Column("desc_ru", sa.Text(), nullable=False),
        sa.Column("desc_en", sa.Text(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("reward_qc", sa.Integer, server_default="1", nullable=False),
        sa.Column("chain_key", sa.String(length=64), nullable=True),
        sa.Column("cooldown_sec", sa.Integer, server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])
    op.create_index("ix_tasks_active_chain", "tasks", ["is_active", "chain_key"])

    op.create_table(
        "user_tasks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint("uq_user_task", "user_tasks", ["user_id", "task_id"])
    op.create_index("ix_user_tasks_user_status", "user_tasks", ["user_id", "status"])

    op.create_table(
        "qc_wallets",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("balance_qc", sa.Integer, server_default="0", nullable=False),
        sa.Column("total_earned_qc", sa.Integer, server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "referrals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("referrer_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("referee_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_referrals_referee", "referrals", ["referee_id"], unique=True)

    op.create_table(
        "withdrawals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount_qc", sa.Integer, nullable=False),
        sa.Column("country", sa.String(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("details", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )

def downgrade():
    op.drop_table("withdrawals")
    op.drop_index("ix_referrals_referee", table_name="referrals")
    op.drop_table("referrals")
    op.drop_table("qc_wallets")
    op.drop_index("ix_user_tasks_user_status", table_name="user_tasks")
    op.drop_constraint("uq_user_task", "user_tasks", type_="unique")
    op.drop_table("user_tasks")
    op.drop_index("ix_tasks_active_chain", table_name="tasks")
    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_uuid", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_index("ix_users_tg_id", table_name="users")
    op.drop_table("users")
