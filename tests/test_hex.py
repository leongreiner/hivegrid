import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_hex as X  # noqa: E402


def neighbours(q, r):
    return {(q + dq, r + dr) for dq, dr in ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))}


def dist(a, b):
    dq, dr = a[0] - b[0], a[1] - b[1]
    return max(abs(dq), abs(dr), abs(dq + dr))


class Spiral(unittest.TestCase):
    def test_ring_counts(self):
        self.assertEqual([len(X.ring(k)) for k in range(5)], [1, 6, 12, 18, 24])

    def test_spiral_prefix_sizes_and_order(self):
        s = X.spiral(19)
        self.assertEqual(s[0], (0, 0))
        self.assertEqual({dist(c, (0, 0)) for c in s[1:7]}, {1})
        self.assertEqual({dist(c, (0, 0)) for c in s[7:19]}, {2})
        self.assertEqual(len(set(s)), 19)
        self.assertEqual(X.spiral(4), s[:4])
        self.assertEqual(X.spiral(0), [])

    def test_ring_is_a_closed_walk(self):
        for k in (1, 2, 3):
            ring = X.ring(k)
            for a, b in zip(ring, ring[1:] + ring[:1]):
                self.assertEqual(dist(a, b), 1, (k, a, b))

    def test_second_slot_is_right_of_the_first(self):
        self.assertEqual(X.spiral(2)[1], (1, 0))

    def test_rings_for(self):
        self.assertEqual([X.rings_for(n) for n in (0, 1, 2, 7, 8, 19, 20)], [0, 0, 1, 1, 2, 2, 3])


class Shapes(unittest.TestCase):
    def test_shape_symmetric_and_in_box(self):
        for ry in range(2, 8):
            pw, h, rows, region = X.shape(ry)
            self.assertEqual(pw, 2 * ry + 2)
            self.assertEqual(list(region), list(reversed(region)), ry)
            for a, b in region:
                self.assertTrue(0 <= a < b <= pw)
                self.assertEqual(a + b, pw, "centred on the box")
            for span, reg in zip(rows, region):
                if span:
                    self.assertTrue(reg[0] < span[0] and span[1] < reg[1], "interior leaves a margin")

    def test_hex_sizes_are_readable(self):
        sizes = X.hex_sizes()
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        for ry in sizes:
            self.assertGreaterEqual(X.interior_width(ry), X.MIN_INTERIOR)

    def test_shapes_tile_without_overlap(self):
        for ry in X.hex_sizes():
            pw, h, rows, region = X.shape(ry)
            owner = {}
            for q, r in X.spiral(37):
                ox, oy = X.origin(q, r, ry)
                for i, span in enumerate(region):
                    for c in range(*span):
                        key = (ox + c, oy + i)
                        self.assertNotIn(key, owner, (ry, (q, r), owner.get(key)))
                        owner[key] = (q, r)


class Geometry(unittest.TestCase):
    def test_origin_centre_round_trip(self):
        for ry in X.hex_sizes():
            pw, h, _, _ = X.shape(ry)
            for q, r in X.spiral(37):
                cx, cy = X.centre(q, r, ry)
                x, y = X.origin(q, r, ry)
                self.assertEqual((cx, cy), (x + pw / 2, y + h / 2))
                # the characters next to the centre belong to the hex
                self.assertEqual(X.hit_test(int(cx), int(cy), ry), (q, r))
                self.assertEqual(X.hit_test(int(cx) - 1, int(cy) - 1, ry), (q, r))

    def test_origin_is_half_pitch_per_row(self):
        ry = 3
        pw = X.pitch(ry)
        self.assertEqual(X.origin(0, 1, ry), (pw // 2, ry))
        self.assertEqual(X.origin(1, 0, ry), (pw, 0))
        self.assertEqual(X.origin(-1, 1, ry), (-pw // 2, ry))

    def test_hit_test_every_drawn_character_incl_borders(self):
        for ry in X.hex_sizes():
            _, _, rows, region = X.shape(ry)
            for q, r in X.spiral(19):
                ox, oy = X.origin(q, r, ry)
                for i, (a, b) in enumerate(region):
                    for c in range(a, b):  # whole region: interior and the border ring
                        self.assertEqual(X.hit_test(ox + c, oy + i, ry), (q, r), (ry, q, r, c, i))
                    self.assertNotEqual(X.hit_test(ox + a - 1, oy + i, ry), (q, r), "just outside the left border")
                    self.assertNotEqual(X.hit_test(ox + b, oy + i, ry), (q, r), "just outside the right border")

    def test_cube_round_ties_are_deterministic_and_valid(self):
        self.assertEqual(X.cube_round(0.2, 0.1), (0, 0))
        self.assertEqual(X.cube_round(0.9, 0.05), (1, 0))
        self.assertEqual(X.cube_round(-0.6, 1.1), (-1, 1))
        for i in range(-20, 21):
            for j in range(-20, 21):
                q, r = X.cube_round(i / 7, j / 7)
                self.assertIsInstance(q, int)
                self.assertIsInstance(r, int)

    def test_bounding_box_covers_every_cell(self):
        for ry in X.hex_sizes():
            pw, h = X.cell_size(ry)
            coords = X.spiral(7)
            bx, by, bw, bh = X.bounding_box(coords, ry)
            for q, r in coords:
                x, y = X.origin(q, r, ry)
                self.assertGreaterEqual(x, bx)
                self.assertGreaterEqual(y, by)
                self.assertLessEqual(x + pw, bx + bw)
                self.assertLessEqual(y + h, by + bh)
            self.assertEqual(X.bounding_box([], ry), (0, 0, 0, 0))
            self.assertEqual(X.bounding_box([(0, 0)], ry)[2:], (pw, h))


class Fit(unittest.TestCase):
    AREAS = [(80, 24), (160, 50), (100, 30), (40, 14), (30, 10), (60, 18)]

    def test_never_exceeds_the_area_and_cells_are_distinct(self):
        for w, h in self.AREAS:
            for n in range(1, 40):
                cw, ch, cells = X.fit(n, w, h)
                if not cw:
                    continue
                self.assertEqual(len(cells), n)
                for q, r, x, y in cells:
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(x + cw, w, (w, h, n))
                    self.assertLessEqual(y + ch, h, (w, h, n))
                self.assertEqual(len({(q, r) for q, r, _, _ in cells}), n)

    def test_monotone_in_n(self):
        for w, h in self.AREAS:
            last = None
            for n in range(1, 60):
                cw, ch, _ = X.fit(n, w, h)
                if last is not None:
                    self.assertLessEqual(cw, last, (w, h, n))
                last = cw

    def test_largest_size_that_fits(self):
        w, h = 80, 24
        cw, ch, _ = X.fit(7, w, h)
        bigger = [ry for ry in X.hex_sizes() if X.pitch(ry) > cw]
        for ry in bigger:
            bx, by, bw, bh = X.bounding_box(X.spiral(7), ry)
            self.assertTrue(bw > w or bh > h)

    def test_centred(self):
        w, h = 100, 40
        cw, ch, cells = X.fit(1, w, h)
        (_, _, x, y), = cells
        self.assertEqual(x, (w - cw) // 2)
        self.assertEqual(y, (h - ch) // 2)

    def test_nothing_fits_returns_empty(self):
        self.assertEqual(X.fit(50, 20, 6), (0, 0, []))
        self.assertEqual(X.fit(1, 5, 3), (0, 0, []))

    def test_ry_of_inverts_cell_width(self):
        for ry in range(X.MIN_RY, X.MAX_RY + 1):
            self.assertEqual(X.ry_of(X.cell_size(ry)[0]), ry)
        with self.assertRaises(ValueError):
            X.ry_of(7)


class Tiles(unittest.TestCase):
    def test_tiles_fit_or_report_it(self):
        for w, h in ((80, 20), (30, 8), (12, 3), (160, 46)):
            for n in (1, 5, 13, 40):
                tw, th, cols, rows, cells, fits = X.fit_tiles(n, w, h)
                self.assertEqual(len(cells), n)
                if fits:
                    for i, x, y in cells:
                        self.assertGreaterEqual(x, 0)
                        self.assertLessEqual(x + tw, w)
                        self.assertLessEqual(y + th, h)

    def test_largest_tiles_for_few_agents(self):
        tw, th, cols, rows, cells, fits = X.fit_tiles(2, 160, 46)
        self.assertEqual((tw, th), X.TILE_SIZES[0])
        self.assertTrue(fits)

    def test_small_area_picks_small_tiles_and_may_not_fit(self):
        tw, th, cols, rows, cells, fits = X.fit_tiles(30, 20, 4)
        self.assertEqual((tw, th), X.TILE_SIZES[-1])
        self.assertFalse(fits)
        self.assertEqual(X.fit_tiles(3, 2, 1)[0], 0)


CELLS = ((8, 17), (10, 21), (7, 15))  # cell pixel sizes: 1080p, a larger font, a small one


def inside(poly, x, y, eps=1e-6):
    ext = X.x_extent(poly, y)
    return ext is not None and ext[0] - eps <= x <= ext[1] + eps


class Pixels(unittest.TestCase):
    def test_hex_polygon_is_regular_in_lattice_proportions(self):
        for cw, ch in CELLS:
            for ry in X.hex_sizes():
                poly = X.hex_polygon(ry, cw, ch)
                a, t = X.hex_extent(ry, cw, ch)
                xs, ys = [p[0] for p in poly], [p[1] for p in poly]
                self.assertAlmostEqual(max(xs) - min(xs), 2 * a)
                self.assertAlmostEqual(max(ys) - min(ys), 2 * t)
                # centred on the hex's centre as hit_test and centre() see it
                pw, h, _, _ = X.shape(ry)
                self.assertAlmostEqual((max(xs) + min(xs)) / 2, pw * cw / 2)
                self.assertAlmostEqual((max(ys) + min(ys)) / 2, h * ch / 2)

    def test_neighbours_share_edges_so_the_gap_is_even(self):
        """The polygons tile the plane: a neighbour's vertices coincide with this hex's, so
        insetting both by the same amount leaves the same gap on every side."""
        for cw, ch in CELLS:
            for ry in X.hex_sizes():
                mine = {(round(x, 6), round(y, 6)) for x, y in X.hex_polygon(ry, cw, ch)}
                for q, r in ((1, 0), (0, 1), (-1, 1)):
                    ox, oy = X.origin(q, r, ry)
                    theirs = {(round(x + ox * cw, 6), round(y + oy * ch, 6)) for x, y in X.hex_polygon(ry, cw, ch)}
                    self.assertEqual(len(mine & theirs), 2, (cw, ch, ry, q, r))

    def test_polygon_agrees_with_hit_test(self):
        """Every character whose centre lies in the drawn hex is a click on that hex."""
        for cw, ch in CELLS:
            for ry in X.hex_sizes():
                poly = X.hex_polygon(ry, cw, ch)
                pw, h, _, _ = X.shape(ry)
                for y in range(-2, h + 2):
                    for x in range(-2, pw + 2):
                        if inside(X.inset(poly, 0.01), (x + 0.5) * cw, (y + 0.5) * ch):
                            self.assertEqual(X.hit_test(x, y, ry), (0, 0), (cw, ch, ry, x, y))

    def test_inset_moves_every_edge_by_d(self):
        poly = X.hex_polygon(6, 8, 17)
        for d in (1.0, 3.5, 7.0):
            inner = X.inset(poly, d)
            for i in range(6):
                (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % 6]
                nx, ny = y1 - y0, -(x1 - x0)
                n = (nx * nx + ny * ny) ** 0.5
                for px, py in (inner[i], inner[(i + 1) % 6]):
                    self.assertAlmostEqual(((x0 - px) * nx + (y0 - py) * ny) / n, d, places=6)

    def test_rounded_corners_stay_inside_and_keep_the_sides(self):
        poly = X.hex_polygon(8, 8, 17)
        pts = X.rounded(poly, 6.0)
        for x, y in pts:
            self.assertTrue(inside(poly, x, y, 1e-6), (x, y))
        # the vertical sides survive: the outline still reaches the full width
        self.assertAlmostEqual(max(x for x, _ in pts), max(x for x, _ in poly))
        self.assertEqual(X.rounded(poly, 0), poly)

    def test_text_spans_lie_on_flat_fill(self):
        for cw, ch in CELLS:
            for ry in X.hex_sizes():
                for margin in (4.0, 7.0):
                    inner = X.inset(X.hex_polygon(ry, cw, ch), margin)
                    spans = X.text_spans(ry, cw, ch, margin)
                    self.assertEqual(len(spans), X.shape(ry)[1])
                    for i, span in enumerate(spans):
                        if span is None:
                            continue
                        for px in (span[0] * cw, span[1] * cw):
                            for py in (i * ch, (i + 1) * ch):
                                self.assertTrue(inside(inner, px, py), (cw, ch, ry, margin, i, span))

    def test_tile_polygon_leaves_even_gaps(self):
        for tw, th in X.TILE_SIZES:
            if th <= 2:
                continue
            px, py = X.tile_pitch(tw, th)
            poly = X.tile_polygon(tw, th, 8, 17)
            # the lattice cells of neighbouring tiles touch exactly
            self.assertAlmostEqual(poly[1][0] - poly[0][0], px * 8)
            self.assertAlmostEqual(poly[2][1] - poly[1][1], py * 17)


if __name__ == "__main__":
    unittest.main()
