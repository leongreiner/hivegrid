"""Hex geometry for the Hivegrid hive: pure math, no terminal I/O.

Hexes are pointy-top and live on an axial (q, r) lattice. A terminal cell is about twice as tall as
it is wide, so one lattice step is `pw` columns to the right and `ry` rows down (every odd row is
shifted half a step), with pw = 2 * ry + 2. The shape drawn for a hex is *rasterised from the
hit-test*: the characters whose centre rounds to the hex are its region, and the interior is that
region minus one column on each side, which leaves a visible gap between neighbours. Drawing and
clicking therefore always agree, and no two hexes ever share a character.

Coordinates in character cells: x to the right, y down, a cell's box top-left is
(pw * q + (pw // 2) * r, ry * r).
"""
import functools
import math

# Ring walk from the corner (k, 0): SW, W, NW, NE, E, SE; that is clockwise on a y-down screen.
RING_DIRS = ((-1, 1), (-1, 0), (0, -1), (1, -1), (1, 0), (0, 1))
MIN_RY, MAX_RY = 3, 8
# Hex cells narrower than this (interior columns at the widest row) are not readable: use tiles.
MIN_INTERIOR = 6


def ring(k):
    """The 6k axial coordinates at distance k from the centre, clockwise from the east corner."""
    if k == 0:
        return [(0, 0)]
    out = []
    q, r = k, 0
    for dq, dr in RING_DIRS:
        for _ in range(k):
            out.append((q, r))
            q, r = q + dq, r + dr
    return out


def spiral(n):
    """The first n axial coordinates of the spiral: the centre, ring 1 (6 cells), ring 2 (12), ..."""
    out, k = [], 0
    while len(out) < n:
        out.extend(ring(k))
        k += 1
    return out[:n]


def rings_for(n):
    """Rings needed to hold n cells (0 for n <= 1)."""
    k, total = 0, 1
    while total < n:
        k += 1
        total += 6 * k
    return k


def pitch(ry):
    return 2 * ry + 2


def cube_round(q, r):
    """Nearest hex of the fractional axial coordinate (q, r)."""
    x, z = q, r
    y = -x - z
    rx, ry_, rz = math.floor(x + 0.5), math.floor(y + 0.5), math.floor(z + 0.5)
    dx, dy, dz = abs(rx - x), abs(ry_ - y), abs(rz - z)
    if dx > dy and dx > dz:
        rx = -ry_ - rz
    elif dy > dz:
        ry_ = -rx - rz
    else:
        rz = -rx - ry_
    return rx, rz


def _round_rel(dx, dy, pw, ry):
    """Hex of the point (dx, dy) measured from the centre of hex (0, 0), in characters."""
    v = dy / ry
    return cube_round(dx / pw - v / 2, v)


@functools.lru_cache(maxsize=None)
def shape(ry):
    """-> (pw, H, rows, region) for lattice row-pitch ry. rows[i] = (x0, x1) columns of the interior
    of the hex at box row i (x1 exclusive, None when the row has no interior); region[i] = the same
    for the whole hex region. H is the box height in rows."""
    pw = pitch(ry)
    extent = int(2 * ry / 3 + 2)
    region = []
    for k in range(-extent, extent):
        dy = k + 0.5
        cols = [c for c in range(pw) if _round_rel(c + 0.5 - pw / 2, dy, pw, ry) == (0, 0)]
        if cols:
            region.append((cols[0], cols[-1] + 1))
    members = [set(range(a, b)) for a, b in region]
    rows = []
    for (a, b), mem in zip(region, members):
        inner = [c for c in range(a, b) if c - 1 in mem and c + 1 in mem]
        rows.append((inner[0], inner[-1] + 1) if inner else None)
    return pw, len(region), tuple(rows), tuple(region)


def cell_size(ry):
    """-> (pw, H): lattice column pitch and box height of the hex for row pitch ry."""
    pw, h, _, _ = shape(ry)
    return pw, h


def interior_width(ry):
    return max((b - a for r in shape(ry)[2] if r for a, b in [r]), default=0)


def hex_sizes():
    """Usable (large to small) row pitches, hex mode only."""
    return [ry for ry in range(MAX_RY, MIN_RY - 1, -1) if interior_width(ry) >= MIN_INTERIOR]


def origin(q, r, ry):
    """Top-left (x, y) of the box of hex (q, r), relative to the box of hex (0, 0)."""
    pw = pitch(ry)
    return pw * q + (pw // 2) * r, ry * r


def centre(q, r, ry):
    """Centre of hex (q, r) as floats in character units, relative to the box of hex (0, 0)."""
    pw, h, _, _ = shape(ry)
    x, y = origin(q, r, ry)
    return x + pw / 2, y + h / 2


def hit_test(x, y, ry):
    """The (q, r) of the hex under character (x, y), coordinates relative to the box of hex (0, 0).
    Every character belongs to some hex of the infinite lattice; callers decide whether that hex
    exists."""
    pw, h, _, _ = shape(ry)
    return _round_rel(x + 0.5 - pw / 2, y + 0.5 - h / 2, pw, ry)


def bounding_box(coords, ry):
    """(min_x, min_y, width, height) of the boxes of the given hexes, in characters."""
    pw, h, _, _ = shape(ry)
    if not coords:
        return 0, 0, 0, 0
    xs = [origin(q, r, ry)[0] for q, r in coords]
    ys = [origin(q, r, ry)[1] for q, r in coords]
    return min(xs), min(ys), max(xs) - min(xs) + pw, max(ys) - min(ys) + h


def fit(n, width, height, sizes=None):
    """Largest hex size that fits n cells in width x height characters, centred in the area.
    -> (cell_w, cell_h, [(q, r, x, y)]) where cell_w is the lattice column pitch, cell_h the box
    height and (x, y) the box top-left of slot i in area coordinates, in spiral order. Returns
    (0, 0, []) when even the smallest hex does not fit (use fit_tiles then)."""
    coords = spiral(max(n, 1))
    for ry in sizes or hex_sizes():
        bx, by, bw, bh = bounding_box(coords, ry)
        if bw <= width and bh <= height:
            ox, oy = (width - bw) // 2 - bx, (height - bh) // 2 - by
            pw, h = cell_size(ry)
            out = []
            for q, r in coords:
                x, y = origin(q, r, ry)
                out.append((q, r, x + ox, y + oy))
            return pw, h, out
    return 0, 0, []


def ry_of(cell_w):
    """Row pitch of the hex size whose lattice column pitch is cell_w (as returned by fit)."""
    ry = (cell_w - 2) // 2
    if pitch(ry) != cell_w or not MIN_RY <= ry <= MAX_RY:
        raise ValueError(f"no hex size with column pitch {cell_w}")
    return ry


# ---- plain tiles --------------------------------------------------------------------

# (width, height) of a tile; tiles are separated by one column, and by one row when taller than 2.
TILE_SIZES = ((22, 5), (18, 4), (14, 3), (11, 3), (9, 2), (7, 2), (5, 1))


def tile_pitch(tw, th):
    return tw + 1, th + (1 if th > 2 else 0)


def fit_tiles(n, width, height):
    """Largest tile size whose grid holds n tiles. -> (tw, th, cols, rows, [(i, x, y)], fits).
    The grid is centred horizontally and starts at the top; `fits` is False when even the smallest
    tile leaves some of the n tiles off the area (the caller scrolls by `rows`)."""
    n = max(n, 1)
    best = None
    for tw, th in TILE_SIZES:
        px, py = tile_pitch(tw, th)
        cols = max((width + 1) // px, 0)
        rows = max((height + (py - th)) // py, 0)
        if cols < 1 or rows < 1:
            continue
        best = (tw, th, cols, rows)
        if cols * rows >= n:
            break
    if best is None:
        return 0, 0, 0, 0, [], False
    tw, th, cols, rows = best
    px, py = tile_pitch(tw, th)
    used = min(n, cols)
    ox = (width - (used * px - 1)) // 2
    return tw, th, cols, rows, [(i, ox + (i % cols) * px, (i // cols) * py) for i in range(n)], cols * rows >= n
