"""批27-5：pool 退避重置的存活时长判据（全局检视 B-P1-3）。

原实现只判活着即重置 backoff——坏 bot spawn 后 30s 内存活就被重置，恒 60s 拉起循环。
修法：spawned_at（每次 Popen 处赋值）+ 存活 ≥POLL_S 才重置。"""
from unittest.mock import MagicMock, patch


def _child(backoff=120.0):
    from src.im_bot import pool
    c = pool._Child(1, "feishu")
    c.backoff = backoff
    c.proc = MagicMock()
    c.proc.poll.return_value = None   # 活着
    return c


def test_short_lived_not_reset():
    """spawn 后 <POLL_S 存活：不重置（坏 bot 秒活不豁免）。"""
    c = _child(backoff=120.0)
    c.spawned_at = 100.0
    with patch("src.im_bot.pool.time.monotonic", return_value=100.0 + 5):   # 仅活 5s < POLL_S(30)
        c.note_healthy()
    assert c.backoff == 120.0


def test_full_cycle_alive_resets():
    """存活 ≥POLL_S：重置（长跑后一次崩溃不按累加倍数罚——原设计语义兑现）。"""
    c = _child(backoff=120.0)
    c.spawned_at = 100.0
    with patch("src.im_bot.pool.time.monotonic", return_value=100.0 + 31):   # 活 31s ≥ POLL_S
        c.note_healthy()
    assert c.backoff != 120.0   # 已重置为 _BACKOFF_BASE


def test_respawn_updates_spawned_at():
    """凭证重起（ensure 再次 Popen）：spawned_at 更新——新进程秒崩不被旧时刻误判健康。

    场景推演（盲审 A）：旧进程跑了 1h（spawned_at 早）→ 换凭证 ensure 重 spawn → 新进程
    5s 崩 → 若 spawned_at 不更新，note_healthy 会按旧时刻判"存活满周期"重置退避。"""
    from src.im_bot import pool
    c = _child(backoff=60.0)
    c.spawned_at = 100.0
    with patch.object(pool.subprocess, "Popen", return_value=MagicMock(pid=42)) as mp, \
         patch("src.im_bot.pool.time.monotonic", return_value=9999.0):   # 重起时刻远晚于旧 spawned_at
        c.ensure("fp-new")
    assert mp.called, "应重 spawn（凭证变更路径）"
    assert c.spawned_at == 9999.0, "每次 Popen 必须刷新 spawned_at"
