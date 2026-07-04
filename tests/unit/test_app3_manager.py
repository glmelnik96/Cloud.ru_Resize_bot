"""TaskManager lane lifecycle: idle lanes must be dismantled, not leak forever.

Before the fix user_queues/user_sems/user_workers grew unboundedly — one
queue+semaphore+worker-task per distinct user_id for the life of the process.
"""
import asyncio

from app.tasks.manager import TaskManager


async def _wait_gone(m: TaskManager, user_id: str, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if user_id not in m.user_queues:
            return
        await asyncio.sleep(0.02)


async def test_idle_lane_is_cleaned_up(tmp_path):
    m = TaskManager(tmp_root=tmp_path, lane_idle_ttl=0.05)
    ran = asyncio.Event()

    async def runner():
        ran.set()

    assert await m.submit("u1", runner)
    await asyncio.wait_for(ran.wait(), 2)

    await _wait_gone(m, "u1")
    assert "u1" not in m.user_queues
    assert "u1" not in m.user_sems
    assert "u1" not in m.user_workers
    await m.shutdown()


async def test_lane_recreated_after_cleanup(tmp_path):
    m = TaskManager(tmp_root=tmp_path, lane_idle_ttl=0.05)
    first = asyncio.Event()

    async def r1():
        first.set()

    assert await m.submit("u1", r1)
    await asyncio.wait_for(first.wait(), 2)
    await _wait_gone(m, "u1")
    assert "u1" not in m.user_queues

    second = asyncio.Event()

    async def r2():
        second.set()

    assert await m.submit("u1", r2)
    await asyncio.wait_for(second.wait(), 2)
    await m.shutdown()


async def test_busy_lane_survives_idle_ttl(tmp_path):
    """A lane with an in-flight task must NOT be dismantled by the idle timer."""
    m = TaskManager(tmp_root=tmp_path, lane_idle_ttl=0.05)
    release = asyncio.Event()
    done = asyncio.Event()

    async def slow():
        await release.wait()
        done.set()

    assert await m.submit("u1", slow)
    # let several idle-ttl periods elapse while the task is still running
    await asyncio.sleep(0.2)
    assert "u1" in m.user_queues

    release.set()
    await asyncio.wait_for(done.wait(), 2)
    await _wait_gone(m, "u1")
    assert "u1" not in m.user_queues
    await m.shutdown()
