"""Pure-Pillow Tech-Tree & Dependency Graph Renderer for dgg-pm.

Generates high-resolution Civilization-style tech trees as PNG buffers directly
usable in Discord messages. Handles layered DAG depth calculation, dummy slot
routing around intermediate nodes, barycenter edge crossing reduction, smooth
cubic Bézier curve routing with directional arrowheads, and dynamic status theming.
"""

from __future__ import annotations

import io
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.domain.tech_tree import TechTree

from PIL import Image, ImageDraw, ImageFont

# --- Color Palette & Aesthetics (Modern Technical Blueprint) ---
BG_COLOR = "#0a1128"
GRID_COLOR = "#152244"
BANNER_BG_START = "#001f54"
BORDER_LINE = "#1282a2"

THEME: dict[str, dict[str, str]] = {
    "complete": {
        "fill": "#0a2e1d",
        "edge": "#57f287",
        "glow": "#15803d",
        "text": "#f0fdf4",
        "desc": "#bbf7d0",
        "accent": "#57f287",
        "badge_bg": "#14532d",
        "badge_text": "#86efac",
    },
    "active": {
        "fill": "#062846",
        "edge": "#38bdf8",
        "glow": "#0369a1",
        "text": "#f0f9ff",
        "desc": "#bae6fd",
        "accent": "#38bdf8",
        "badge_bg": "#0c4a6e",
        "badge_text": "#7dd3fc",
    },
    "available": {
        "fill": "#102a5c",
        "edge": "#818cf8",
        "glow": "#4338ca",
        "text": "#e0e7ff",
        "desc": "#c7d2fe",
        "accent": "#818cf8",
        "badge_bg": "#1e3a8a",
        "badge_text": "#93c5fd",
    },
    "locked": {
        "fill": "#0a101f",
        "edge": "#334155",
        "glow": "#1e293b",
        "text": "#94a3b8",
        "desc": "#64748b",
        "accent": "#64748b",
        "badge_bg": "#1e293b",
        "badge_text": "#94a3b8",
    },
    "blocked": {
        "fill": "#3b0d0c",
        "edge": "#ed4245",
        "glow": "#991b1b",
        "text": "#fef2f2",
        "desc": "#fecaca",
        "accent": "#ed4245",
        "badge_bg": "#7f1d1d",
        "badge_text": "#fca5a5",
    },
}

LABEL: dict[str, str] = {
    "complete": "COMPLETED",
    "active": "IN PROGRESS",
    "available": "READY TO START",
    "locked": "LOCKED",
    "blocked": "BLOCKED",
}

NODE_W = 320
NODE_H = 185
DUMMY_W = 36
DUMMY_H = 28
H_GAP = 95
V_GAP = 36
MAX_EDGE = 3800
PAD = 48
TITLE_H = 96

# Font search paths: check bundled assets folder first
_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def _load_font(bold: bool, size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Loads bundled TrueType fonts with robust fallback to system fonts."""
    font_name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    bundled_path = _FONT_DIR / font_name
    if bundled_path.is_file():
        try:
            return ImageFont.truetype(str(bundled_path), size)
        except OSError:
            pass

    fallback_names = (
        ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial-Bold.ttf", "arialbd.ttf")
        if bold
        else ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf", "arial.ttf")
    )
    search_roots = (
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/TTF",
        "/usr/share/fonts/truetype/liberation",
        "/nix/var/nix/profiles/default/share/fonts",
        "C:/Windows/Fonts",
        "/System/Library/Fonts",
        "",
    )
    for root in search_roots:
        for name in fallback_names:
            try:
                path = f"{root}/{name}" if root else name
                return ImageFont.truetype(path, size)
            except OSError:
                continue

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


F_TITLE = _load_font(True, 30)
F_SUBTITLE = _load_font(False, 15)
F_BADGE = _load_font(True, 12)
F_KEY = _load_font(True, 13)
F_NAME = _load_font(True, 16)
F_DESC = _load_font(False, 13)
F_PILL = _load_font(True, 12)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, max_w: int, max_lines: int = 2) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            if len(lines) >= max_lines:
                break
            cur = w
    if len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and len(lines) < len(words):
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_w:
            last = last[:-1]
        lines[-1] = (last + "…") if last != lines[-1] else lines[-1]
    return lines


# --- Layer Calculation & Layout Planning ---


def compute_layers(nodes: list[dict[str, Any]], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Computes column/layer index for each node using longest path from root in DAG."""
    prereqs: dict[str, list[str]] = {n["key"]: [] for n in nodes}
    for src, dst in edges:
        if dst in prereqs and src in prereqs:
            prereqs[dst].append(src)

    depth: dict[str, int] = {}
    visiting: set[str] = set()

    def walk(k: str) -> int:
        if k in depth:
            return depth[k]
        if k in visiting:  # cycle safeguard
            return 0
        visiting.add(k)
        d = 0 if not prereqs[k] else 1 + max(walk(p) for p in prereqs[k])
        visiting.discard(k)
        depth[k] = d
        return d

    for n in nodes:
        walk(n["key"])
    return depth


def plan_layout(
    nodes: list[dict[str, Any]], edges: list[tuple[str, str]]
) -> tuple[dict[int, list[str]], dict[tuple[str, str], list[str]], dict[str, int]]:
    """Assigns nodes and dummy routing slots to layers and orders them to minimize crossings."""
    depth = compute_layers(nodes, edges)
    layers: dict[int, list[str]] = {}
    for n in nodes:
        layers.setdefault(depth[n["key"]], []).append(n["key"])

    routing: list[tuple[str, str]] = []
    chains: dict[tuple[str, str], list[str]] = {}
    for src, dst in edges:
        if src not in depth or dst not in depth:
            continue
        d0, d1 = depth[src], depth[dst]
        if d1 - d0 <= 1:
            routing.append((src, dst))
            chains[(src, dst)] = []
            continue
        chain: list[str] = []
        prev = src
        for d in range(d0 + 1, d1):
            dk = f"\x00{src}>{dst}@{d}"
            depth[dk] = d
            layers.setdefault(d, []).append(dk)
            routing.append((prev, dk))
            chain.append(dk)
            prev = dk
        routing.append((prev, dst))
        chains[(src, dst)] = chain

    preds: dict[str, list[str]] = {}
    succs: dict[str, list[str]] = {}
    for a, b in routing:
        preds.setdefault(b, []).append(a)
        succs.setdefault(a, []).append(b)

    order = {d: list(ks) for d, ks in layers.items()}
    idx: dict[str, int] = {}

    def reindex() -> None:
        idx.clear()
        for ks in order.values():
            for i, k in enumerate(ks):
                idx[k] = i

    reindex()

    # Barycenter crossing minimization sweeps
    max_d = max(order.keys(), default=0)
    for _ in range(5):
        for d in range(1, max_d + 1):
            scored = []
            for k in order[d]:
                ps = preds.get(k, [])
                score = sum(idx[p] for p in ps if p in idx) / len(ps) if ps else idx.get(k, 0)
                scored.append((score, k))
            scored.sort(key=lambda t: t[0])
            order[d] = [k for _, k in scored]
            reindex()

        for d in range(max_d - 1, -1, -1):
            scored = []
            for k in order[d]:
                ss = succs.get(k, [])
                score = sum(idx[s] for s in ss if s in idx) / len(ss) if ss else idx.get(k, 0)
                scored.append((score, k))
            scored.sort(key=lambda t: t[0])
            order[d] = [k for _, k in scored]
            reindex()

    return order, chains, depth


def _bezier_points(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    steps: int = 28,
) -> list[tuple[int, int]]:
    """Generates discrete sampled points along a cubic Bézier spline."""
    pts: list[tuple[int, int]] = []
    for i in range(steps + 1):
        t = i / steps
        t2 = t * t
        t3 = t2 * t
        mt = 1 - t
        mt2 = mt * mt
        mt3 = mt2 * mt

        x = mt3 * p0[0] + 3 * mt2 * t * p1[0] + 3 * mt * t2 * p2[0] + t3 * p3[0]
        y = mt3 * p0[1] + 3 * mt2 * t * p1[1] + 3 * mt * t2 * p2[1] + t3 * p3[1]
        pts.append((round(x), round(y)))
    return pts


def render_tree(
    nodes: list[dict[str, Any]],
    edges: list[tuple[str, str]],
    title: str = "Project Tech Tree",
    subtitle: str | None = None,
    mode: str = "lr",
) -> io.BytesIO:
    """Renders a project dependency graph into an in-memory PNG BytesIO buffer."""
    tb = mode == "tb"
    by_key = {n["key"]: n for n in nodes}

    # Handle empty state
    if not nodes:
        img = Image.new("RGB", (750, 280), BG_COLOR)
        draw = ImageDraw.Draw(img)
        draw.text((375, 100), title, font=F_TITLE, fill="#f8fafc", anchor="mm")
        draw.text(
            (375, 160),
            "No tasks found in this project.",
            font=F_SUBTITLE,
            fill="#64748b",
            anchor="mm",
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf

    order, chains, depth = plan_layout(nodes, edges)
    num_layers = max(order.keys(), default=0) + 1

    # Compute layer spans
    layer_sizes: dict[int, int] = {}
    for d, ks in order.items():
        if tb:
            layer_sizes[d] = sum(DUMMY_W if k.startswith("\x00") else NODE_W for k in ks) + max(0, len(ks) - 1) * V_GAP
        else:
            layer_sizes[d] = sum(DUMMY_H if k.startswith("\x00") else NODE_H for k in ks) + max(0, len(ks) - 1) * V_GAP
    max_layer_span = max(layer_sizes.values(), default=NODE_W if tb else NODE_H)

    # Canvas dimensions
    if tb:
        min_w = 850
        width = PAD * 2 + max(max_layer_span, min_w - PAD * 2)
        height = PAD * 2 + TITLE_H + num_layers * NODE_H + max(0, num_layers - 1) * H_GAP
    else:
        width = PAD * 2 + max(num_layers * NODE_W + max(0, num_layers - 1) * H_GAP, 850)
        height = PAD * 2 + TITLE_H + max_layer_span

    img = Image.new("RGB", (width, height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Grid background pattern
    for gx in range(0, width, 48):
        draw.line([(gx, 0), (gx, height)], fill=GRID_COLOR, width=1)
    for gy in range(0, height, 48):
        draw.line([(0, gy), (width, gy)], fill=GRID_COLOR, width=1)

    # Header Banner
    draw.rectangle([0, 0, width, TITLE_H], fill=BANNER_BG_START)
    draw.line([(0, TITLE_H), (width, TITLE_H)], fill=BORDER_LINE, width=2)

    # Title & Subtitle / Stats
    completed_n = sum(1 for n in nodes if n.get("state") == "complete")
    active_n = sum(1 for n in nodes if n.get("state") == "active")
    avail_n = sum(1 for n in nodes if n.get("state") == "available")
    locked_n = sum(1 for n in nodes if n.get("state") == "locked")
    pct_val = int((completed_n / len(nodes)) * 100) if nodes else 0

    # Progress bar on banner right
    prog_w = min(240, width // 4)
    prog_x = width - PAD - prog_w
    prog_y = 38

    # Prevent title from colliding with progress bar
    max_title_w = prog_x - PAD - 24
    disp_title = title
    if draw.textlength(disp_title, font=F_TITLE) > max_title_w:
        while disp_title and draw.textlength(disp_title + "…", font=F_TITLE) > max_title_w:
            disp_title = disp_title[:-1]
        disp_title = (disp_title + "…") if disp_title != title else title

    draw.text((PAD, 22), disp_title, font=F_TITLE, fill="#ffffff")
    if subtitle:
        stats_text = subtitle
    else:
        stats_text = (
            f"{len(nodes)} Tasks  •  {completed_n} Complete  •  {active_n} In Progress  •  "
            f"{avail_n} Ready  •  {locked_n} Locked  •  {pct_val}% Done"
        )
    draw.text((PAD, 62), stats_text, font=F_SUBTITLE, fill="#94a3b8")

    draw.rounded_rectangle([prog_x, prog_y, prog_x + prog_w, prog_y + 18], radius=8, fill="#1e293b")
    if pct_val > 0:
        fill_px = max(14, int(prog_w * (pct_val / 100)))
        draw.rounded_rectangle([prog_x, prog_y, prog_x + fill_px, prog_y + 18], radius=8, fill="#22c55e")
    draw.text((prog_x + prog_w // 2, prog_y + 9), f"{pct_val}%", font=F_BADGE, fill="#ffffff", anchor="mm")

    # Node coordinate calculation
    coords: dict[str, tuple[int, int, int, int]] = {}
    for d, ks in order.items():
        if tb:
            layer_w = sum(DUMMY_W if k.startswith("\x00") else NODE_W for k in ks) + max(0, len(ks) - 1) * V_GAP
            cur_x = (width - layer_w) // 2
            cur_y = PAD + TITLE_H + d * (NODE_H + H_GAP)
            for k in ks:
                is_dummy = k.startswith("\x00")
                w_k = DUMMY_W if is_dummy else NODE_W
                h_k = DUMMY_H if is_dummy else NODE_H
                coords[k] = (cur_x, cur_y, cur_x + w_k, cur_y + h_k)
                cur_x += w_k + V_GAP
        else:
            layer_h = sum(DUMMY_H if k.startswith("\x00") else NODE_H for k in ks) + max(0, len(ks) - 1) * V_GAP
            cur_x = PAD + d * (NODE_W + H_GAP)
            cur_y = PAD + TITLE_H + (max_layer_span - layer_h) // 2
            for k in ks:
                is_dummy = k.startswith("\x00")
                w_k = DUMMY_W if is_dummy else NODE_W
                h_k = DUMMY_H if is_dummy else NODE_H
                coords[k] = (cur_x, cur_y, cur_x + w_k, cur_y + h_k)
                cur_y += h_k + V_GAP

    def box(k: str) -> tuple[int, int, int, int]:
        return coords.get(k, (0, 0, NODE_W, NODE_H))

    def mid_x(k: str) -> int:
        b = box(k)
        return (b[0] + b[2]) // 2

    def mid_y(k: str) -> int:
        b = box(k)
        return (b[1] + b[3]) // 2

    # Group edges by source and target for port offset calculations
    out_edges: dict[str, list[str]] = {}
    in_edges: dict[str, list[str]] = {}
    for s, t in edges:
        if s in depth and t in depth:
            out_edges.setdefault(s, []).append(t)
            in_edges.setdefault(t, []).append(s)

    # Sort target nodes by vertical position so fan-out lines don't cross each other at port
    for s in out_edges:
        out_edges[s].sort(key=lambda t: mid_y(t) if not tb else mid_x(t))
    for t in in_edges:
        in_edges[t].sort(key=lambda s: mid_y(s) if not tb else mid_x(s))

    # --- Draw Edges with Smooth Bézier Splines & Arrowheads ---
    for src, dst in edges:
        if src not in depth or dst not in depth:
            continue

        src_state = by_key.get(src, {}).get("state", "locked")
        if src_state == "complete":
            edge_color = "#22c55e"
            edge_width = 3
        elif src_state == "active":
            edge_color = "#38bdf8"
            edge_width = 3
        elif src_state == "available":
            edge_color = "#818cf8"
            edge_width = 2
        elif src_state == "blocked":
            edge_color = "#ef4444"
            edge_width = 2
        else:
            # Clearly visible slate for locked/pending prerequisites
            edge_color = "#64748b"
            edge_width = 2

        # Compute port offsets
        s_list = out_edges.get(src, [])
        s_idx = s_list.index(dst) if dst in s_list else 0
        s_offset = (s_idx - (len(s_list) - 1) / 2) * 12 if len(s_list) > 1 else 0

        d_list = in_edges.get(dst, [])
        d_idx = d_list.index(src) if src in d_list else 0
        d_offset = (d_idx - (len(d_list) - 1) / 2) * 12 if len(d_list) > 1 else 0

        if tb:
            start_pt = (mid_x(src) + int(s_offset), box(src)[3])
            waypoints = [start_pt]
            for dk in chains.get((src, dst), []):
                _, ly0, _, ly1 = box(dk)
                waypoints.extend([(mid_x(dk), ly0), (mid_x(dk), ly1)])
            end_pt = (mid_x(dst) + int(d_offset), box(dst)[1])
            waypoints.append(end_pt)
        else:
            start_pt = (box(src)[2], mid_y(src) + int(s_offset))
            waypoints = [start_pt]
            for dk in chains.get((src, dst), []):
                lx0, _, lx1, _ = box(dk)
                waypoints.extend([(lx0, mid_y(dk)), (lx1, mid_y(dk))])
            end_pt = (box(dst)[0], mid_y(dst) + int(d_offset))
            waypoints.append(end_pt)

        # Draw smooth Bézier curve segments through waypoints
        for (ax, ay), (bx, by) in pairwise(waypoints):
            if tb:
                dy = by - ay
                p0 = (float(ax), float(ay))
                p1 = (float(ax), float(ay + dy * 0.5))
                p2 = (float(bx), float(by - dy * 0.5))
                p3 = (float(bx), float(by))
            else:
                dx = bx - ax
                p0 = (float(ax), float(ay))
                p1 = (float(ax + dx * 0.5), float(ay))
                p2 = (float(bx - dx * 0.5), float(by))
                p3 = (float(bx), float(by))

            curve_pts = _bezier_points(p0, p1, p2, p3, steps=32)
            draw.line(curve_pts, fill=edge_color, width=edge_width, joint="curve")

        # Source Port Anchor Dot
        sx, sy = waypoints[0]
        draw.ellipse([sx - 4, sy - 4, sx + 4, sy + 4], fill=edge_color)

        # Target Port Arrowhead Indicator
        tx, ty = waypoints[-1]
        if tb:
            draw.polygon([(tx, ty), (tx - 6, ty - 9), (tx + 6, ty - 9)], fill=edge_color)
        else:
            draw.polygon([(tx, ty), (tx - 9, ty - 6), (tx - 9, ty + 6)], fill=edge_color)

    # --- Draw Task Cards ---
    for n in nodes:
        k = n["key"]
        state = n.get("state", "locked")
        t = THEME.get(state, THEME["locked"])
        x0, y0, x1, y1 = box(k)
        inner_w = NODE_W - 36

        # Card shadow & background container
        draw.rounded_rectangle([x0 + 4, y0 + 6, x1 + 4, y1 + 6], radius=12, fill="#040609")
        draw.rounded_rectangle([x0, y0, x1, y1], radius=12, fill=t["fill"], outline=t["edge"], width=2)

        # Left accent stripe
        draw.rounded_rectangle([x0, y0, x0 + 6, y1], radius=4, fill=t["accent"])

        # Top Bar: Status Badge
        badge_label = LABEL.get(state, "LOCKED")
        tag_w = draw.textlength(badge_label, font=F_BADGE)
        draw.rounded_rectangle([x0 + 16, y0 + 14, x0 + 28 + tag_w, y0 + 34], radius=6, fill=t["badge_bg"])
        draw.text((x0 + 22, y0 + 17), badge_label, font=F_BADGE, fill=t["badge_text"])

        # Top Bar: Short ID tag (e.g., [TREE-1])
        short_id = n.get("short_id", k)
        draw.text((x1 - 16, y0 + 17), short_id, font=F_KEY, fill=t["accent"], anchor="ra")

        # Task Name / Title (High contrast & bold)
        cur_y = y0 + 46
        title_lines = _wrap(draw, n.get("name", "Untitled"), F_NAME, inner_w, 2)
        for line in title_lines:
            draw.text((x0 + 16, cur_y), line, font=F_NAME, fill=t["text"])
            cur_y += 22

        # Task Description / Details
        desc = (n.get("description") or "").strip()
        if desc:
            cur_y += 3
            desc_lines = _wrap(draw, desc, F_DESC, inner_w, 2)
            for line in desc_lines:
                if cur_y + 16 > y1 - 42:
                    break
                draw.text((x0 + 16, cur_y), line, font=F_DESC, fill=t["desc"])
                cur_y += 18

        # Bottom Bar: Divider
        bottom_y = y1 - 32
        draw.line([(x0 + 16, bottom_y - 8), (x1 - 16, bottom_y - 8)], fill=BORDER_LINE, width=1)

        # Assignee label
        assignee = n.get("assignee")
        if assignee:
            assignee_str = str(assignee)
            if len(assignee_str) > 16:
                assignee_str = assignee_str[:14] + "…"
            assignee_label = f"Assignee: {assignee_str}"
            draw.text((x0 + 16, bottom_y), assignee_label, font=F_PILL, fill="#cbd5e1")
        else:
            draw.text((x0 + 16, bottom_y), "Unassigned", font=F_PILL, fill="#64748b")

        # Priority Badge on bottom right
        priority = (n.get("priority") or "normal").upper()
        if priority in ("HIGH", "URGENT", "CRITICAL"):
            draw.text((x1 - 16, bottom_y), "HIGH PRIORITY", font=F_PILL, fill="#f87171", anchor="ra")
        elif priority == "LOW":
            draw.text((x1 - 16, bottom_y), "LOW PRIORITY", font=F_PILL, fill="#4ade80", anchor="ra")
        else:
            draw.text((x1 - 16, bottom_y), "NORMAL", font=F_PILL, fill="#94a3b8", anchor="ra")

    # Downscale if exceeding Discord max upload limit
    if max(img.size) > MAX_EDGE:
        scale = MAX_EDGE / max(img.size)
        img = img.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


class PillowTechTreeRenderer:
    """Concrete adapter rendering a TechTree into an in-memory PNG BytesIO buffer using Pillow."""

    def render(
        self,
        tree: TechTree | Any,
        title: str = "Project Tech Tree",
        subtitle: str | None = None,
        mode: str = "lr",
        **kwargs: Any,
    ) -> io.BytesIO:
        if hasattr(tree, "to_nodes_data"):
            nodes, edges = tree.to_nodes_data()
        elif isinstance(tree, tuple) and len(tree) == 2:
            nodes, edges = tree
        else:
            raise TypeError(f"Expected TechTree or (nodes, edges) tuple, got {type(tree)}")
        return render_tree(nodes, edges, title=title, subtitle=subtitle, mode=mode)


class MermaidTechTreeRenderer:
    """Concrete adapter formatting a TechTree into a Mermaid DAG codeblock for Discord text/threads."""

    def render(
        self,
        tree: TechTree,
        orientation: str = "lr",
        **kwargs: Any,
    ) -> str:
        orient_tag = "TB" if orientation.lower() in ("tb", "vertical") else "LR"
        lines = [f"graph {orient_tag}"]

        # Class styles for readiness states
        lines.append("    classDef complete fill:#0a2e1d,stroke:#57f287,stroke-width:2px,color:#f0fdf4;")
        lines.append("    classDef active fill:#062846,stroke:#38bdf8,stroke-width:2px,color:#f0f9ff;")
        lines.append("    classDef available fill:#102a5c,stroke:#818cf8,stroke-width:2px,color:#e0e7ff;")
        lines.append("    classDef locked fill:#0a101f,stroke:#334155,stroke-width:1px,color:#94a3b8;")
        lines.append("    classDef blocked fill:#3b0d0c,stroke:#ed4245,stroke-width:2px,color:#fef2f2;")

        nodes, edges = tree.to_nodes_data() if hasattr(tree, "to_nodes_data") else ([], [])
        for n in nodes:
            key = n["key"]
            name = n["name"].replace('"', "'")
            state = n.get("state", "locked")
            lines.append(f'    {key}["{key}: {name}"]:::{state}')

        for src, dst in edges:
            lines.append(f"    {src} --> {dst}")

        return "\n".join(lines)


# Register defaults onto pure domain TechTree
from src.domain.tech_tree import TechTree  # noqa: E402

TechTree.register_default_renderers(
    png_renderer=PillowTechTreeRenderer(),
    mermaid_renderer=MermaidTechTreeRenderer(),
)
