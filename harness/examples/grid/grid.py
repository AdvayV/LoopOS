"""Grid algorithms used to generate practice tasks for LoopOS loops."""
from __future__ import annotations

from collections import deque

Grid = list[list[int]]
Point = tuple[int, int]
DIRECTIONS = [(0, 1), (1, 0), (0, -1), (-1, 0)]


def parse(text: str) -> Grid:
    """'.#\\n..' -> [[0, 1], [0, 0]] where '#' is a wall."""
    return [[1 if ch == "#" else 0 for ch in line] for line in text.strip().splitlines()]


def in_bounds(grid: Grid, point: Point) -> bool:
    row, col = point
    return 0 <= row < len(grid) and 0 <= col < len(grid[0])


def neighbors(grid: Grid, point: Point) -> list[Point]:
    row, col = point
    found = []
    for d_row, d_col in DIRECTIONS:
        candidate = (row + d_row, col + d_col)
        if in_bounds(grid, candidate) and grid[candidate[0]][candidate[1]] == 0:
            found.append(candidate)
    return found


def shortest_path(grid: Grid, start: Point, goal: Point) -> int:
    """Number of steps from start to goal avoiding walls, or -1 if unreachable."""
    if grid[start[0]][start[1]] == 1 or grid[goal[0]][goal[1]] == 1:
        return -1
    queue = deque([(start, 0)])
    seen = {start}
    while queue:
        point, steps = queue.popleft()
        if point == goal:
            return steps
        for nxt in neighbors(grid, point):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, steps + 1))
    return -1


def flood_fill(grid: Grid, start: Point) -> int:
    """Count the open cells connected to start (including start)."""
    if grid[start[0]][start[1]] == 1:
        return 0
    stack = [start]
    seen = {start}
    while stack:
        point = stack.pop()
        for nxt in neighbors(grid, point):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return len(seen)


def rotate(grid: Grid) -> Grid:
    """Rotate 90 degrees clockwise."""
    rows, cols = len(grid), len(grid[0])
    return [[grid[rows - 1 - r][c] for r in range(rows)] for c in range(cols)]


def count_islands(grid: Grid) -> int:
    """Groups of 4-connected open cells."""
    seen: set[Point] = set()
    islands = 0
    for r in range(len(grid)):
        for c in range(len(grid[0])):
            if grid[r][c] == 0 and (r, c) not in seen:
                islands += 1
                stack = [(r, c)]
                seen.add((r, c))
                while stack:
                    for nxt in neighbors(grid, stack.pop()):
                        if nxt not in seen:
                            seen.add(nxt)
                            stack.append(nxt)
    return islands


def manhattan(a: Point, b: Point) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def border_cells(grid: Grid) -> int:
    rows, cols = len(grid), len(grid[0])
    if rows == 1 or cols == 1:
        return rows * cols
    return 2 * rows + 2 * cols - 4
