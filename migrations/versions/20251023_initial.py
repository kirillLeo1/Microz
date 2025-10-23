from alembic import op
import sqlalchemy as sa

revision = '20251023_initial'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('users',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('tg_id', sa.BigInteger, nullable=False, unique=True, index=True),
        sa.Column('lang', sa.String),
        sa.Column('status', sa.String, index=True),
        sa.Column('referrer_id', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(timezone=True))
    )
    op.create_table('payments',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), index=True),
        sa.Column('uuid', sa.String(64), unique=True, index=True),
        sa.Column('amount_usd', sa.Float),
        sa.Column('status', sa.String(32), index=True),
        sa.Column('created_at', sa.DateTime(timezone=True))
    )
    op.create_table('tasks',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('title_uk', sa.String, nullable=False),
        sa.Column('title_ru', sa.String, nullable=False),
        sa.Column('title_en', sa.String, nullable=False),
        sa.Column('desc_uk', sa.Text, nullable=False),
        sa.Column('desc_ru', sa.Text, nullable=False),
        sa.Column('desc_en', sa.Text, nullable=False),
        sa.Column('url', sa.String, nullable=False),
        sa.Column('reward_qc', sa.Integer, server_default='1'),
        sa.Column('chain_key', sa.String(64), index=True),
        sa.Column('cooldown_sec', sa.Integer, server_default='0'),
        sa.Column('is_active', sa.Boolean, server_default=sa.text('true'), index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), index=True)
    )
    op.create_index('ix_tasks_active_chain', 'tasks', ['is_active','chain_key'])
    op.create_table('user_tasks',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), index=True),
        sa.Column('task_id', sa.Integer, sa.ForeignKey('tasks.id'), index=True),
        sa.Column('status', sa.String, index=True),
        sa.Column('available_at', sa.DateTime(timezone=True), index=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), index=True),
        sa.UniqueConstraint('user_id','task_id', name='uq_user_task')
    )
    op.create_table('qc_wallets',
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('balance_qc', sa.Integer, server_default='0'),
        sa.Column('total_earned_qc', sa.Integer, server_default='0'),
        sa.Column('updated_at', sa.DateTime(timezone=True))
    )
    op.create_table('referrals',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('referrer_id', sa.Integer, sa.ForeignKey('users.id'), index=True),
        sa.Column('referee_id', sa.Integer, sa.ForeignKey('users.id'), unique=True, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True))
    )
    op.create_table('withdrawals',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), index=True),
        sa.Column('amount_qc', sa.Integer, nullable=False),
        sa.Column('country', sa.String, nullable=False),
        sa.Column('method', sa.String, nullable=False),
        sa.Column('details', sa.String),
        sa.Column('status', sa.String, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('processed_at', sa.DateTime(timezone=True))
    )
    op.create_table('kv',
        sa.Column('key', sa.String, primary_key=True),
        sa.Column('value', sa.String, nullable=False)
    )

def downgrade():
    for t in ['kv','withdrawals','referrals','qc_wallets','user_tasks','tasks','payments','users']:
        op.drop_table(t)