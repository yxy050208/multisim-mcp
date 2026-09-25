"""Pin escape and obstacle-aware rectilinear routing for generated schematics.

Geometry is expressed in the normalized symbol coordinate system. Crossings
between unrelated nets cost extra; shared line segments are forbidden. A route
which cannot be drawn raises an error instead of silently crossing a symbol.
"""
from __future__ import annotations

import heapq
from bisect import bisect_left, bisect_right
from functools import lru_cache
from typing import Any

Point = tuple[float, float]


def rectangle(item: dict[str, Any]) -> tuple[float, float, float, float]:
    x, y = float(item["x"]), float(item["y"])
    return x, y, x + float(item.get("width", 126)), y + float(item.get("height", 108))


def crosses_box(a: Point, b: Point, box: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = box
    if a[1] == b[1]:
        return y0 < a[1] < y1 and max(min(a[0], b[0]), x0) < min(max(a[0], b[0]), x1)
    if a[0] == b[0]:
        return x0 < a[0] < x1 and max(min(a[1], b[1]), y0) < min(max(a[1], b[1]), y1)
    raise ValueError("route segment is not orthogonal")


def segment_relation(a: Point, b: Point, c: Point, d: Point) -> str | None:
    """Classify positive-length overlap and crossing/touch of two segments."""
    ah, ch = a[1] == b[1], c[1] == d[1]
    if ah == ch:
        fixed, axis = (1, 0) if ah else (0, 1)
        if a[fixed] != c[fixed]:
            return None
        lo, hi = max(min(a[axis], b[axis]), min(c[axis], d[axis])), min(max(a[axis], b[axis]), max(c[axis], d[axis]))
        return "overlap" if lo < hi else "touch" if lo == hi else None
    h1, h2, v1, v2 = (a, b, c, d) if ah else (c, d, a, b)
    if min(h1[0], h2[0]) <= v1[0] <= max(h1[0], h2[0]) and min(v1[1], v2[1]) <= h1[1] <= max(v1[1], v2[1]):
        p = (v1[0], h1[1])
        return "touch" if p in (a, b, c, d) else "crossing"
    return None


def pin_escape(connection: dict[str, Any], obstacles: list[dict[str, Any]], clearance: float = 9) -> Point:
    """Leave a normalized pin through its nearest symbol edge, never its body."""
    p = (float(connection["x"]), float(connection["y"]))
    owner = next((item for item in obstacles if item.get("refdes") == connection.get("refdes")), None)
    if owner is None:
        return p
    x0, y0, x1, y1 = rectangle(owner)
    direction = connection.get("direction")
    if direction:
        dx,dy = direction
        if dx:
            return (x1+clearance if dx>0 else x0-clearance,p[1])
        if dy:
            return (p[0],y1+clearance if dy>0 else y0-clearance)
    choices = [(abs(p[0] - x0), (x0 - clearance, p[1])),
               (abs(p[0] - x1), (x1 + clearance, p[1])),
               (abs(p[1] - y0), (p[0], y0 - clearance)),
               (abs(p[1] - y1), (p[0], y1 + clearance))]
    return min(choices, key=lambda item: item[0])[1]


def junction_point(connections: list[dict[str, Any]], obstacles: list[dict[str, Any]],
                   occupied: list[tuple[Point, Point]]) -> Point:
    escapes = [pin_escape(c, obstacles) for c in connections]
    xs = {p[0] for p in escapes}
    ys = {p[1] for p in escapes}
    for item in obstacles:
        x0, y0, x1, y1 = rectangle(item)
        xs.update((x0 - 18, x1 + 18))
        ys.update((y0 - 18, y1 + 18))
    boxes = [rectangle(item) for item in obstacles]
    pins = {(float(c["x"]), float(c["y"])) for c in connections}
    candidates = sorted(((x, y) for x in xs for y in ys),
                        key=lambda p: (sum(abs(p[0]-q[0])+abs(p[1]-q[1]) for q in escapes), p))
    for p in candidates:
        if p in pins or any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for x0, y0, x1, y1 in boxes):
            continue
        if any(segment_relation(p, p, a, b) for a, b in occupied):
            continue
        return p
    raise ValueError("no free junction location")


def route(start: Point, end: Point, obstacles: list[dict[str, Any]], *,
          occupied: list[tuple[Point, Point]] | None = None) -> list[Point]:
    """A* on a sparse coordinate grid, with bend and crossing penalties."""
    occupied = occupied or []
    boxes = [rectangle(item) for item in obstacles]
    for p in (start, end):
        if any(x0 < p[0] < x1 and y0 < p[1] < y1 for x0, y0, x1, y1 in boxes):
            raise ValueError(f"routing endpoint inside symbol: {p}")
    xs, ys = {start[0], end[0]}, {start[1], end[1]}
    for x0, y0, x1, y1 in boxes:
        xs.update((x0 - 9, x1 + 9))
        ys.update((y0 - 9, y1 + 9))
    for a, b in occupied:
        if a[0] == b[0]:
            xs.update((a[0] - 9, a[0] + 9))
        else:
            ys.update((a[1] - 9, a[1] + 9))
    # Spatial buckets: a horizontal edge can only ever interact with
    # horizontal segments on its own row and vertical segments whose x falls
    # inside the edge's span (and vice versa).  The buckets are kept as
    # sorted arrays so an edge can pull every segment whose perpendicular
    # coordinate lies inside the edge's span (bisect) -- segments may sit at
    # coordinates that are NOT grid rows/cols (elbow fallback points), so a
    # plain per-grid-line lookup would miss them.  With the buckets, cost()
    # semantics are identical to the old scan-everything loop while cutting
    # it from O(all segments) to O(log n + nearby) -- the difference between
    # minutes and seconds on large custom layouts.
    h_by_row: dict[float, list[tuple[Point, Point]]] = {}
    v_by_col: dict[float, list[tuple[Point, Point]]] = {}
    for a, b in occupied:
        if a[0] == b[0]:
            v_by_col.setdefault(a[0], []).append((a, b))
        else:
            h_by_row.setdefault(a[1], []).append((a, b))
    h_rows = sorted(h_by_row.items())
    h_row_ys = [y for y, _segs in h_rows]
    v_cols = sorted(v_by_col.items())
    v_col_xs = [x for x, _segs in v_cols]

    def _segs_in_range(indexed: list, coords: list, lo: float, hi: float):
        lo, hi = min(lo, hi), max(lo, hi)
        start_i = bisect_left(coords, lo)
        end_i = bisect_right(coords, hi)
        out: list[tuple[Point, Point]] = []
        for _coord, segs in indexed[start_i:end_i]:
            out.extend(segs)
        return out

    xs.update((min(xs)-18, max(xs)+18))
    ys.update((min(ys)-18, max(ys)+18))
    xx, yy = sorted(xs), sorted(ys)
    first = (bisect_left(xx, start[0]), bisect_left(yy, start[1]), 0)
    target = (bisect_left(xx, end[0]), bisect_left(yy, end[1]))
    distances, parents = {first: 0.0}, {}
    queue = [(0.0, 0.0, first)]

    @lru_cache(maxsize=None)
    def cost(x: int, y: int, nx: int, ny: int) -> float:
        a, b = (xx[x], yy[y]), (xx[nx], yy[ny])
        if any(crosses_box(a, b, box) for box in boxes):
            return float("inf")
        if a[1] == b[1]:
            local = list(h_by_row.get(a[1], ()))
            local.extend(_segs_in_range(v_cols, v_col_xs, a[0], b[0]))
        else:
            local = list(v_by_col.get(a[0], ()))
            local.extend(_segs_in_range(h_rows, h_row_ys, a[1], b[1]))
        crossings = 0
        for c, d in local:
            relation = segment_relation(a, b, c, d)
            if relation == "overlap":
                return float("inf")
            if relation == "touch" and (segment_relation(c,c,a,b) or segment_relation(d,d,a,b)):
                # Do not route through another net's junction or bend.
                return float("inf")
            # Never terminate on another net, even when the logical XML
            # would keep it separate: that is visually an ambiguous short.
            if relation and (a in (start, end) or b in (start, end)):
                if segment_relation(start, start, c, d) or segment_relation(end, end, c, d):
                    return float("inf")
            crossings += bool(relation)
        return abs(a[0]-b[0])+abs(a[1]-b[1])+crossings*90

    while queue:
        _, distance, current = heapq.heappop(queue)
        if distance != distances.get(current):
            continue
        x, y, direction = current
        if (x, y) == target:
            points = []
            while True:
                points.append((xx[current[0]], yy[current[1]]))
                if current == first:
                    break
                current = parents[current]
            points.reverse()
            return simplify(points)
        for nx, ny, nd in ((x-1,y,1),(x+1,y,1),(x,y-1,2),(x,y+1,2)):
            if not (0 <= nx < len(xx) and 0 <= ny < len(yy)):
                continue
            new = (nx, ny, nd)
            value = distance + cost(x, y, nx, ny) + (18 if direction and direction != nd else 0)
            if value < distances.get(new, float("inf")):
                distances[new], parents[new] = value, current
                estimate = abs(xx[nx]-end[0])+abs(yy[ny]-end[1])
                heapq.heappush(queue, (value+estimate, value, new))
    raise ValueError(f"no obstacle-free wire route from {start} to {end}")


def simplify(points: list[Point]) -> list[Point]:
    result: list[Point] = []
    for p in points:
        if result and p == result[-1]:
            continue
        if len(result) > 1 and ((result[-2][0] == result[-1][0] == p[0]) or (result[-2][1] == result[-1][1] == p[1])):
            a, b = result[-2], result[-1]
            if min(a[0],p[0]) <= b[0] <= max(a[0],p[0]) and min(a[1],p[1]) <= b[1] <= max(a[1],p[1]):
                result.pop()
        result.append(p)
    return result


def route_pins(start: dict[str, Any], end: dict[str, Any], obstacles: list[dict[str, Any]],
               occupied: list[tuple[Point, Point]] | None = None) -> list[Point]:
    a, b = (float(start["x"]), float(start["y"])), (float(end["x"]), float(end["y"]))
    ea, eb = pin_escape(start, obstacles), pin_escape(end, obstacles)
    for p, e, conn in ((a,ea,start),(b,eb,end)):
        blockers = [item for item in obstacles if item.get("refdes") != conn.get("refdes") and crosses_box(p,e,rectangle(item))]
        if blockers:
            raise ValueError(f"pin escape {conn.get('refdes')} {p}->{e} crosses obstacle {blockers[0]}")
        if any(segment_relation(p,e,c,d) == "overlap" for c,d in occupied or []):
            raise ValueError("pin escape overlaps another net")
    return simplify([a, *route(ea, eb, obstacles, occupied=occupied), b])
