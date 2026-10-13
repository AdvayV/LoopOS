"""A priority job queue with aging and deadlines, used to generate practice tasks for LoopOS loops."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Job:
    name: str
    priority: int
    duration: int
    deadline: int | None = None
    waited: int = 0
    submitted: int = 0


@dataclass
class JobQueue:
    aging: int = 3            # +1 effective priority per `aging` turns waited
    jobs: list[Job] = field(default_factory=list)
    clock: int = 0
    _order: int = 0

    def submit(self, job: Job) -> None:
        if job.duration <= 0:
            raise ValueError("duration must be positive")
        if job.priority < 0 or job.priority > 10:
            raise ValueError("priority must be between 0 and 10")
        self._order += 1
        job.submitted = self._order
        self.jobs.append(job)

    def effective(self, job: Job) -> int:
        return job.priority + job.waited // self.aging

    def pick(self) -> Job | None:
        """Highest effective priority; ties go to the longest waiter, then submission order."""
        if not self.jobs:
            return None
        best = max(self.jobs, key=lambda j: (self.effective(j), j.waited, -j.submitted))
        for job in self.jobs:
            if job is not best:
                job.waited += 1
        self.jobs.remove(best)
        return best

    def run(self) -> list[str]:
        """Run everything to completion; returns the names in execution order."""
        order = []
        while (job := self.pick()) is not None:
            self.clock += job.duration
            order.append(job.name)
        return order


def missed_deadlines(jobs: list[Job], order: list[str]) -> list[str]:
    """Names of jobs that finish after their deadline when run in `order`."""
    by_name = {job.name: job for job in jobs}
    clock = 0
    late = []
    for name in order:
        job = by_name[name]
        clock += job.duration
        if job.deadline is not None and clock > job.deadline:
            late.append(name)
    return late


def time_slices(duration: int, quantum: int) -> list[int]:
    """Round-robin slices for one job: [quantum, quantum, ..., remainder]."""
    if quantum <= 0:
        raise ValueError("quantum must be positive")
    full, rest = divmod(duration, quantum)
    return [quantum] * full + ([rest] if rest else [])


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Half-open intervals [start, end) overlap."""
    return a_start < b_end and b_start < a_end


def utilisation(busy: list[tuple[int, int]], horizon: int) -> int:
    """Percentage of [0, horizon) covered by the busy intervals (which may overlap)."""
    covered = 0
    for t in range(horizon):
        if any(start <= t < end for start, end in busy):
            covered += 1
    return covered * 100 // horizon
