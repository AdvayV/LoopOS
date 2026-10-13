import pytest

from jobqueue import Job, JobQueue, missed_deadlines, overlaps, time_slices, utilisation


def test_submit_validates():
    queue = JobQueue()
    with pytest.raises(ValueError):
        queue.submit(Job("x", 5, 0))
    with pytest.raises(ValueError):
        queue.submit(Job("x", 11, 1))
    queue.submit(Job("ok", 10, 1))
    assert queue.jobs[0].submitted == 1


def test_pick_prefers_priority_then_waiting_then_order():
    queue = JobQueue()
    queue.submit(Job("low", 1, 1))
    queue.submit(Job("high", 5, 1))
    queue.submit(Job("high2", 5, 1))
    assert queue.pick().name == "high"
    assert queue.jobs[0].waited == 1 and queue.jobs[1].waited == 1
    assert queue.pick().name == "high2"
    assert queue.pick().name == "low"
    assert queue.pick() is None


def test_aging_lets_low_priority_run():
    queue = JobQueue(aging=2)
    queue.submit(Job("low", 1, 1))
    for n in range(8):
        queue.submit(Job(f"high{n}", 5, 1))
    # low gains +1 effective priority every 2 picks: after 8 waits it reaches 5 and wins the tie
    # against the remaining high jobs because it has waited longest.
    assert queue.run() == ["high0", "high1", "high2", "high3", "high4", "high5", "high6", "high7", "low"]
    # New high-priority work keeps arriving: without aging "low" would starve. With aging=2 its
    # effective priority reaches 3 after four waits and the tie goes to the longest waiter.
    queue2 = JobQueue(aging=2)
    queue2.submit(Job("low", 1, 1))
    picked = []
    for n in range(5):
        queue2.submit(Job(f"high{n}", 3, 1))
        picked.append(queue2.pick().name)
    assert picked == ["high0", "high1", "high2", "high3", "low"]


def test_run_advances_clock():
    queue = JobQueue()
    queue.submit(Job("a", 1, 3))
    queue.submit(Job("b", 2, 4))
    assert queue.run() == ["b", "a"]
    assert queue.clock == 7


def test_missed_deadlines():
    jobs = [Job("a", 1, 3, deadline=3), Job("b", 1, 4, deadline=6), Job("c", 1, 1)]
    assert missed_deadlines(jobs, ["a", "b", "c"]) == ["b"]
    assert missed_deadlines(jobs, ["b", "a", "c"]) == ["a"]


def test_time_slices():
    assert time_slices(7, 3) == [3, 3, 1]
    assert time_slices(6, 3) == [3, 3]
    assert time_slices(2, 5) == [2]
    with pytest.raises(ValueError):
        time_slices(5, 0)


def test_overlaps_half_open():
    assert overlaps(0, 5, 4, 6)
    assert not overlaps(0, 5, 5, 6)
    assert not overlaps(5, 6, 0, 5)
    assert overlaps(0, 10, 2, 3)


def test_utilisation():
    assert utilisation([(0, 5)], 10) == 50
    assert utilisation([(0, 5), (3, 8)], 10) == 80
    assert utilisation([], 10) == 0
    assert utilisation([(0, 10), (0, 10)], 10) == 100
