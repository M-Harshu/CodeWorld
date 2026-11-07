"""Code-city layout: directories become districts, files become buildings (height = complexity, footprint = LOC)."""
from __future__ import annotations
import math


def _squarify(items: list[dict], x: float, z: float, w: float, d: float) -> None:
    """Slice-and-dice treemap (alternating direction). Mutates items with x,z,w,d."""
    total = sum(i["area"] for i in items) or 1.0
    horizontal = w >= d
    cx, cz = x, z
    for it in sorted(items, key=lambda i: -i["area"]):
        frac = it["area"] / total
        if horizontal:
            it.update(x=cx, z=z, w=w * frac, d=d); cx += w * frac
        else:
            it.update(x=x, z=cz, w=w, d=d * frac); cz += d * frac


def layout(ctx) -> dict:
    g = ctx.graph
    with ctx.con() as con:
        git = {r["path"]: r["commits"] for r in con.execute("SELECT path, commits FROM git_file")}
    tree: dict = {"name": "", "children": {}, "files": []}
    for f in g.files():
        node = tree
        parts = f["path"].split("/")
        for p in parts[:-1]:
            node = node["children"].setdefault(p, {"name": p, "children": {}, "files": []})
        node["files"].append(f)

    def weight(n) -> float:
        return sum(max(5, f["props"].get("loc", 5)) for f in n["files"]) + sum(weight(c) for c in n["children"].values())

    buildings, districts = [], []

    def place(n, x, z, w, d, depth):
        items = [{"kind": "file", "f": f, "area": max(5, f["props"].get("loc", 5))} for f in n["files"]]
        items += [{"kind": "dir", "n": c, "area": weight(c)} for c in n["children"].values()]
        if not items:
            return
        pad = 0.04 * min(w, d)
        _squarify(items, x, z, w, d)
        for it in items:
            ix, iz, iw, idp = it["x"] + pad, it["z"] + pad, max(0.01, it["w"] - 2 * pad), max(0.01, it["d"] - 2 * pad)
            if it["kind"] == "file":
                f = it["f"]
                cx = f["props"].get("complexity", 1)
                buildings.append({"path": f["path"], "name": f["name"], "x": ix, "z": iz, "w": iw * 0.9, "d": idp * 0.9,
                                  "height": round(1 + math.log1p(cx) * 3, 2), "loc": f["props"].get("loc", 0), "complexity": cx,
                                  "commits": git.get(f["path"], 0), "is_test": bool(f["props"].get("is_test")), "language": f["props"].get("language"),
                                  "top": f["path"].split("/")[0] if "/" in f["path"] else "(root)"})
            else:
                districts.append({"name": it["n"]["name"], "x": ix, "z": iz, "w": iw, "d": idp, "depth": depth})
                place(it["n"], ix, iz, iw, idp, depth + 1)

    size = 100.0
    place(tree, 0, 0, size, size, 0)
    return {"size": size, "buildings": buildings, "districts": districts}
