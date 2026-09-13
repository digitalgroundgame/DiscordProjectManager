"""Pure-Pillow & Mermaid Gantt Chart Renderer for DGG-PM Project Timeline.

Generates high-resolution technical blueprint-themed Gantt charts as PNG byte buffers
and Mermaid markdown strings for Discord. Features auto-adaptive time scaling (days/weeks/months),
status-colored execution bars, dependency connectors, and a 'Today' indicator line.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from src.domain.timeline import ProjectTimeline

# Aesthetic Palette (Matching Tech Tree Blueprint)
BG_COLOR = "#0a1128"
GRID_COLOR = "#152244"
BANNER_BG = "#001f54"
BORDER_LINE = "#1282a2"
TODAY_LINE = "#f59e0b"

THEME: dict[str, dict[str, str]] = {
    "complete": {
        "fill": "#0a2e1d",
        "edge": "#57f287",
        "text": "#f0fdf4",
        "badge_bg": "#14532d",
        "badge_text": "#86efac",
    },
    "active": {
        "fill": "#062846",
        "edge": "#38bdf8",
        "text": "#f0f9ff",
        "badge_bg": "#0c4a6e",
        "badge_text": "#7dd3fc",
    },
    "available": {
        "fill": "#102a5c",
        "edge": "#818cf8",
        "text": "#e0e7ff",
        "badge_bg": "#1e3a8a",
        "badge_text": "#93c5fd",
    },
    "locked": {
        "fill": "#0a101f",
        "edge": "#334155",
        "text": "#94a3b8",
        "badge_bg": "#1e293b",
        "badge_text": "#94a3b8",
    },
    "blocked": {
        "fill": "#3b0d0c",
        "edge": "#ed4245",
        "text": "#fef2f2",
        "badge_bg": "#7f1d1d",
        "badge_text": "#fca5a5",
    },
}

_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def _load_font(bold: bool, size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Loads bundled TrueType fonts with robust system fallbacks."""
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
    return ImageFont.load_default()


class PillowGanttRenderer:
    """Renders a ProjectTimeline into a high-resolution PNG image buffer."""

    def render(
        self,
        timeline: ProjectTimeline,
        title: str = "Project Timeline",
        subtitle: str | None = None,
    ) -> io.BytesIO:
        tasks = list(timeline.tasks)
        f_title = _load_font(bold=True, size=22)
        f_subtitle = _load_font(bold=False, size=13)
        f_header = _load_font(bold=True, size=11)
        f_task = _load_font(bold=True, size=12)
        f_small = _load_font(bold=False, size=10)

        pad = 32
        banner_h = 80
        time_ruler_h = 44
        row_h = 42
        label_col_w = 260
        min_timeline_w = 600

        # Handle empty timeline edge case
        if not tasks:
            img_w = 800
            img_h = 240
            img = Image.new("RGB", (img_w, img_h), BG_COLOR)
            draw = ImageDraw.Draw(img)
            draw.text((pad, 24), title, font=f_title, fill="#ffffff")
            draw.text((pad, 60), "No tasks recorded for this project.", font=f_subtitle, fill="#94a3b8")
            draw.rectangle([pad, 100, img_w - pad, img_h - pad], outline=GRID_COLOR, width=1)
            draw.text((img_w // 2, 150), "Timeline is empty", font=f_subtitle, fill="#64748b", anchor="mm")
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            buf.seek(0)
            return buf

        t_start = timeline.start_date or datetime.now(UTC)
        t_end = timeline.end_date or (t_start + timedelta(days=1))
        # Ensure a minimum 2-day visual range
        if (t_end - t_start).days < 2:
            t_end = t_start + timedelta(days=2)

        total_days = max(1, (t_end - t_start).days)

        # Adaptive tick scale selection
        if total_days <= 21:
            scale_mode = "days"
            tick_step = 1
            day_px = 32
        elif total_days <= 90:
            scale_mode = "weeks"
            tick_step = 7
            day_px = 12
        else:
            scale_mode = "months"
            tick_step = 30
            day_px = 4

        timeline_area_w = max(min_timeline_w, total_days * day_px)
        timeline_area_w = min(timeline_area_w, 2400)

        img_w = pad * 2 + label_col_w + timeline_area_w
        content_h = len(tasks) * row_h
        img_h = pad * 2 + banner_h + time_ruler_h + content_h

        img = Image.new("RGB", (img_w, img_h), BG_COLOR)
        draw = ImageDraw.Draw(img)

        # 1. Background Blueprint Grid
        for gx in range(0, img_w, 40):
            draw.line([(gx, 0), (gx, img_h)], fill=GRID_COLOR, width=1)
        for gy in range(0, img_h, 40):
            draw.line([(0, gy), (img_w, gy)], fill=GRID_COLOR, width=1)

        # 2. Header Banner
        draw.text((pad, 20), title, font=f_title, fill="#ffffff")
        completed_n = sum(1 for t in tasks if t.status == "completed")
        pct_val = int((completed_n / len(tasks)) * 100)
        sub_text = (
            subtitle or f"{len(tasks)} Tasks  •  {completed_n} Completed  •  {total_days} Days Span  •  {pct_val}% Done"
        )
        draw.text((pad, 54), sub_text, font=f_subtitle, fill="#94a3b8")

        # Progress Pill
        prog_w = 140
        prog_x = img_w - pad - prog_w
        prog_y = 30
        draw.rounded_rectangle([prog_x, prog_y, prog_x + prog_w, prog_y + 16], radius=8, fill="#1e293b")
        if pct_val > 0:
            fill_px = max(10, int(prog_w * (pct_val / 100)))
            draw.rounded_rectangle([prog_x, prog_y, prog_x + fill_px, prog_y + 16], radius=8, fill="#22c55e")
        draw.text((prog_x + prog_w // 2, prog_y + 8), f"{pct_val}%", font=f_small, fill="#ffffff", anchor="mm")

        # 3. Time Ruler Coordinates
        tl_x0 = pad + label_col_w
        tl_x1 = tl_x0 + timeline_area_w
        tl_y0 = pad + banner_h
        tl_y1 = tl_y0 + time_ruler_h

        # Label Column Header
        draw.rectangle([pad, tl_y0, tl_x0, tl_y1], fill="#071329", outline=BORDER_LINE, width=1)
        draw.text((pad + 12, tl_y0 + 14), "TASK IDENTIFIER & TITLE", font=f_header, fill="#94a3b8")

        # Timeline Header Box
        draw.rectangle([tl_x0, tl_y0, tl_x1, tl_y1], fill="#071329", outline=BORDER_LINE, width=1)

        def time_to_x(dt: datetime) -> int:
            dt_clean = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
            t_start_clean = t_start if t_start.tzinfo else t_start.replace(tzinfo=UTC)
            offset = (dt_clean - t_start_clean).total_seconds()
            total_sec = max(1.0, (t_end - t_start_clean).total_seconds())
            ratio = max(0.0, min(1.0, offset / total_sec))
            return int(tl_x0 + ratio * timeline_area_w)

        # Draw Tick Marks & Dates
        curr_dt = t_start
        while curr_dt <= t_end:
            tx = time_to_x(curr_dt)
            draw.line([(tx, tl_y0), (tx, img_h - pad)], fill="#1a2b56", width=1)
            if scale_mode == "days":
                tick_label = curr_dt.strftime("%b %d")
            elif scale_mode == "weeks":
                tick_label = curr_dt.strftime("Wk %U")
            else:
                tick_label = curr_dt.strftime("%b %Y")
            draw.text((tx + 4, tl_y0 + 14), tick_label, font=f_small, fill="#64748b")
            curr_dt += timedelta(days=tick_step)

        # 4. Draw Rows & Task Bars
        task_bar_coords: dict[Any, tuple[int, int, int, int]] = {}

        now_utc = datetime.now(UTC)
        show_today = t_start <= now_utc <= t_end
        today_x = time_to_x(now_utc) if show_today else None

        for idx, ttask in enumerate(tasks):
            ry0 = tl_y1 + idx * row_h
            ry1 = ry0 + row_h
            state_key = ttask.state.value if hasattr(ttask.state, "value") else str(ttask.state)
            theme = THEME.get(state_key, THEME["available"])

            # Alternating row background
            if idx % 2 == 1:
                draw.rectangle([pad, ry0, tl_x1, ry1], fill="#081024")

            # Left Label Column Info
            short_id_tag = f"[{ttask.short_id}]"
            draw.text((pad + 10, ry0 + 8), short_id_tag, font=f_task, fill=theme["edge"])
            title_text = ttask.title[:24] + "…" if len(ttask.title) > 24 else ttask.title
            draw.text((pad + 10, ry0 + 24), title_text, font=f_small, fill="#cbd5e1")

            # Timeline Execution Bar
            bx0 = max(tl_x0, time_to_x(ttask.start_date))
            bx1 = min(tl_x1, max(bx0 + 12, time_to_x(ttask.end_date)))
            by0 = ry0 + 9
            by1 = by0 + 24

            task_bar_coords[ttask.id] = (bx0, by0, bx1, by1)

            # Draw bar fill & border
            draw.rounded_rectangle([bx0, by0, bx1, by1], radius=4, fill=theme["fill"], outline=theme["edge"], width=2)

            # Bar inner text (e.g. duration or short ID)
            bar_w = bx1 - bx0
            if bar_w > 48:
                dur_text = f"{ttask.duration_days}d"
                draw.text((bx0 + 6, by0 + 5), dur_text, font=f_small, fill=theme["text"])
            if ttask.assignee_name and bar_w > 110:
                draw.text((bx1 - 6, by0 + 5), f"@{ttask.assignee_name}", font=f_small, fill="#93c5fd", anchor="ra")

        # 5. Draw Dependency Connectors
        for ttask in tasks:
            if not ttask.prerequisite_ids:
                continue
            cur_box = task_bar_coords.get(ttask.id)
            if not cur_box:
                continue
            for pid in ttask.prerequisite_ids:
                pre_box = task_bar_coords.get(pid)
                if not pre_box:
                    continue
                # Line from pre_box right-middle to cur_box left-middle
                px, py = pre_box[2], (pre_box[1] + pre_box[3]) // 2
                cx, cy = cur_box[0], (cur_box[1] + cur_box[3]) // 2
                mid_x = px + max(8, (cx - px) // 2)
                draw.line([(px, py), (mid_x, py), (mid_x, cy), (cx, cy)], fill="#38bdf8", width=2)
                # Arrowhead pointing right into cx
                draw.polygon([(cx, cy), (cx - 5, cy - 3), (cx - 5, cy + 3)], fill="#38bdf8")

        # 6. Today Marker Line
        if today_x is not None and tl_x0 <= today_x <= tl_x1:
            draw.line([(today_x, tl_y0), (today_x, img_h - pad)], fill=TODAY_LINE, width=2)
            draw.rounded_rectangle([today_x - 22, tl_y0 + 2, today_x + 22, tl_y0 + 16], radius=3, fill=TODAY_LINE)
            draw.text((today_x, tl_y0 + 9), "TODAY", font=f_small, fill="#0a1128", anchor="mm")

        # Outer Frame
        draw.rectangle([pad, tl_y0, tl_x1, img_h - pad], outline=BORDER_LINE, width=1)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf


class MermaidGanttRenderer:
    """Renders a ProjectTimeline into standard Mermaid Gantt markdown syntax."""

    def render(self, timeline: ProjectTimeline) -> str:
        tasks = list(timeline.tasks)
        lines = [
            "gantt",
            "    dateFormat YYYY-MM-DD",
            "    title Project Timeline",
            "    section Tasks",
        ]

        if not tasks:
            lines.append("    Empty Project :2026-01-01, 1d")
            return "\n".join(lines)

        task_keys = {t.id: t.short_id for t in tasks}

        for t in tasks:
            status_prefix = ""
            if t.status == "completed":
                status_prefix = "done, "
            elif t.status == "inProgress":
                status_prefix = "active, "

            clean_title = t.title.replace(":", " - ")
            item_desc = f"[{t.short_id}] {clean_title}"

            # Dependency after
            prereq_keys = [task_keys[pid] for pid in t.prerequisite_ids if pid in task_keys]
            if prereq_keys:
                after_ref = f"after {prereq_keys[0]}, "
            else:
                after_ref = f"{t.start_date.strftime('%Y-%m-%d')}, "

            lines.append(f"    {item_desc} :{status_prefix}{after_ref}{t.duration_days}d")

        return "\n".join(lines)
