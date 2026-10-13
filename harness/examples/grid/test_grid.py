from grid import border_cells, count_islands, flood_fill, manhattan, neighbors, parse, rotate, shortest_path

MAZE = parse("""
..#.
.##.
....
""")


def test_parse_and_neighbors():
    assert MAZE[0] == [0, 0, 1, 0]
    assert neighbors(MAZE, (0, 0)) == [(0, 1), (1, 0)]
    assert neighbors(MAZE, (2, 3)) == [(2, 2), (1, 3)]  # right, down, left, up order
    assert neighbors(MAZE, (1, 1)) == [(2, 1), (1, 0), (0, 1)]  # a wall cell still lists open neighbours


def test_shortest_path():
    assert shortest_path(MAZE, (0, 0), (0, 3)) == 7
    assert shortest_path(MAZE, (0, 0), (0, 0)) == 0
    assert shortest_path(MAZE, (0, 0), (0, 2)) == -1
    assert shortest_path(parse(".#.\n###\n..."), (0, 0), (2, 2)) == -1


def test_flood_fill():
    assert flood_fill(MAZE, (0, 0)) == 9
    assert flood_fill(MAZE, (0, 2)) == 0
    assert flood_fill(parse(".#.\n###\n..."), (2, 0)) == 3


def test_rotate():
    assert rotate([[1, 2], [3, 4]]) == [[3, 1], [4, 2]]
    assert rotate([[1, 2, 3]]) == [[1], [2], [3]]
    assert rotate(rotate(rotate(rotate(MAZE)))) == MAZE


def test_count_islands():
    assert count_islands(MAZE) == 1
    assert count_islands(parse(".#.\n###\n.#.")) == 4
    assert count_islands(parse("###")) == 0


def test_manhattan():
    assert manhattan((0, 0), (2, 3)) == 5
    assert manhattan((2, 3), (0, 0)) == 5


def test_border_cells():
    assert border_cells(MAZE) == 10
    assert border_cells([[0]]) == 1
    assert border_cells([[0, 0, 0]]) == 3
    assert border_cells([[0, 0], [0, 0]]) == 4
