"""Server-rendered inline SVG charts for offline HTML reports.

No JavaScript: each mark carries a native ``<title>`` tooltip, colors come from CSS custom
properties (``--c0`` … ``--c7``) so the page's light/dark theme applies, and every chart in the
report also has a table view. Series colors follow the model's position in the run (fixed order,
never cycled; at most eight models per run are colored, later ones reuse no hue but use the
neutral ``--c-other``).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

MAX_COLORS = 8
W, H = 640, 260
PAD_L, PAD_R, PAD_T, PAD_B = 56, 16, 16, 40


def color(index: int) -> str:
    return f"var(--c{index})" if index < MAX_COLORS else "var(--c-other)"


def fmt(value: float | None, unit: str = "") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    if unit == "ratio":
        return f"{value * 100:.1f}%"
    if float(value).is_integer() and abs(value) < 1e15:
        text = f"{int(value):,}"
        return f"{text} {unit}".strip() if unit and unit not in ("count", "score") else text
    a = abs(value)
    text = f"{value:,.0f}" if a >= 1000 else f"{value:.1f}" if a >= 10 else f"{value:.2f}"
    return f"{text} {unit}".strip() if unit and unit not in ("count", "score") else text


def _t(text: str) -> str:
    return escape(str(text), quote=True)


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    # The last tick is the first one at or above ``hi``, so the axis always covers the data.
    ticks = [round(start, 10)]
    while ticks[-1] < hi - step * 1e-9:
        ticks.append(round(start + len(ticks) * step, 10))
    return ticks


@dataclass
class Series:
    name: str
    index: int  # model position → color
    values: Sequence[float]


def _frame(
    x_ticks: list[float],
    y_ticks: list[float],
    sx,
    sy,
    x_label: str,
    y_label: str,
    width: int = W,
    height: int = H,
    x_fmt=None,
) -> list[str]:
    x_fmt = x_fmt or (lambda v: fmt(v))
    out = []
    for v in y_ticks:
        y = sy(v)
        out.append(
            f'<line class="gr" x1="{PAD_L}" x2="{width - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>'
        )
        out.append(
            f'<text class="tk" x="{PAD_L - 6}" y="{y + 4:.1f}" text-anchor="end">{_t(fmt(v))}</text>'
        )
    for v in x_ticks:
        x = sx(v)
        out.append(
            f'<text class="tk" x="{x:.1f}" y="{height - PAD_B + 16}" text-anchor="middle">{_t(x_fmt(v))}</text>'
        )
    out.append(
        f'<line class="ax" x1="{PAD_L}" x2="{width - PAD_R}" y1="{height - PAD_B}" y2="{height - PAD_B}"/>'
    )
    out.append(
        f'<text class="al" x="{(PAD_L + width - PAD_R) / 2:.1f}" y="{height - 6}" text-anchor="middle">{_t(x_label)}</text>'
    )
    out.append(
        f'<text class="al" transform="translate(14 {(PAD_T + height - PAD_B) / 2:.1f}) rotate(-90)" text-anchor="middle">{_t(y_label)}</text>'
    )
    return out


def _svg(body: list[str], title: str, width: int = W, height: int = H) -> str:
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{_t(title)}" '
        f'xmlns="http://www.w3.org/2000/svg">{"".join(body)}</svg>'
    )


def _empty(title: str, message: str = "No data to chart.") -> str:
    return f'<p class="empty" aria-label="{_t(title)}">{_t(message)}</p>'


def histogram(series: Sequence[Series], title: str, unit: str, bins: int = 20) -> str:
    """Small multiples: one histogram per model on a shared x-scale."""
    populated = [s for s in series if s.values]
    if not populated:
        return _empty(title)
    lo = min(min(s.values) for s in populated)
    hi = max(max(s.values) for s in populated)
    if hi <= lo:
        hi = lo + 1
    width = (hi - lo) / bins
    panels = []
    ph = 150
    for s in populated:
        counts = [0] * bins
        for v in s.values:
            counts[min(int((v - lo) / width), bins - 1)] += 1
        top = max(counts)
        y_ticks = nice_ticks(0, top, 3)
        ymax = y_ticks[-1] or 1
        x_ticks = nice_ticks(lo, hi, 5)
        x_ticks = [t for t in x_ticks if lo <= t <= hi]

        def sx(v: float) -> float:
            return PAD_L + (v - lo) / (hi - lo) * (W - PAD_L - PAD_R)

        def sy(v: float, ymax: float = ymax) -> float:
            return PAD_T + (1 - v / ymax) * (ph - PAD_T - PAD_B)

        body = _frame(x_ticks, y_ticks, sx, sy, f"{title} ({unit})", "requests", height=ph)
        bar_w = (W - PAD_L - PAD_R) / bins
        for i, c in enumerate(counts):
            if not c:
                continue
            x0 = PAD_L + i * bar_w + 1
            y = sy(c)
            body.append(
                f'<rect x="{x0:.1f}" y="{y:.1f}" width="{max(bar_w - 2, 1):.1f}" '
                f'height="{ph - PAD_B - y:.1f}" rx="2" style="fill:{color(s.index)}">'
                f"<title>{_t(s.name)}: {_t(fmt(lo + i * width))}–{_t(fmt(lo + (i + 1) * width))} "  # noqa: RUF001
                f"{_t(unit)} · {c} request(s)</title></rect>"
            )
        panels.append(
            f'<figure class="panel"><figcaption><span class="key" style="background:{color(s.index)}">'
            f"</span>{_t(s.name)} · n={len(s.values)}</figcaption>"
            f"{_svg(body, f'{title}: {s.name}', height=ph)}</figure>"
        )
    return '<div class="multiples">' + "".join(panels) + "</div>"


def _legend(entries: Sequence[tuple[str, int]]) -> str:
    if len(entries) < 2:
        return ""
    items = "".join(
        f'<li><span class="key" style="background:{color(i)}"></span>{_t(n)}</li>'
        for n, i in entries
    )
    return f'<ul class="legend">{items}</ul>'


def line_chart(
    series: Sequence[tuple[str, int, Sequence[tuple[float, float | None]]]],
    title: str,
    x_label: str,
    y_label: str,
    y_unit: str = "",
) -> str:
    """One line per model; ``None`` values break the line (missing is not zero)."""
    pts = [(x, y) for _, _, p in series for x, y in p if y is not None]
    if not pts:
        return _empty(title)
    xmax = max(x for x, _ in pts) or 1
    y_ticks = nice_ticks(0, max(y for _, y in pts) or 1, 4)
    ymax = y_ticks[-1] or 1
    x_ticks = [t for t in nice_ticks(0, xmax, 6) if t <= xmax]

    def sx(v: float) -> float:
        return PAD_L + v / xmax * (W - PAD_L - PAD_R)

    def sy(v: float) -> float:
        return PAD_T + (1 - v / ymax) * (H - PAD_T - PAD_B)

    body = _frame(x_ticks, y_ticks, sx, sy, x_label, y_label)
    for name, idx, points in series:
        seg: list[str] = []
        segments: list[list[str]] = []
        for x, y in points:
            if y is None:
                if seg:
                    segments.append(seg)
                seg = []
                continue
            seg.append(f"{sx(x):.1f},{sy(y):.1f}")
        if seg:
            segments.append(seg)
        for s in segments:
            body.append(
                f'<polyline points="{" ".join(s)}" fill="none" stroke-width="2" '
                f'stroke-linejoin="round" style="stroke:{color(idx)}"/>'
            )
        for x, y in points:
            if y is None:
                continue
            body.append(
                f'<g><circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="8" class="hit"/>'
                f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3" class="ring" style="fill:{color(idx)}"/>'
                f"<title>{_t(name)} · {_t(fmt(x))} s: {_t(fmt(y, y_unit))}</title></g>"
            )
    return _legend([(n, i) for n, i, _ in series]) + _svg(body, title)


def grouped_bars(
    categories: Sequence[str],
    series: Sequence[tuple[str, int, Sequence[float | None]]],
    title: str,
    y_label: str,
    unit: str = "ratio",
) -> str:
    """Bars per category, one per model. Missing values draw no bar and say so in the tooltip."""
    if not categories or not series:
        return _empty(title)
    values = [v for _, _, vs in series for v in vs if v is not None]
    if not values:
        return _empty(title, "No value could be measured.")
    ymax = 1.0 if unit == "ratio" else (nice_ticks(0, max(values) or 1, 4)[-1] or 1)
    y_ticks = nice_ticks(0, ymax, 4)
    height = H + 30
    group_w = (W - PAD_L - PAD_R) / len(categories)
    bar_w = min(28.0, (group_w - 12) / len(series))

    def sy(v: float) -> float:
        return PAD_T + (1 - v / ymax) * (height - PAD_T - PAD_B - 30)

    base = height - PAD_B - 30
    body = []
    for v in y_ticks:
        y = sy(v)
        body.append(f'<line class="gr" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        body.append(
            f'<text class="tk" x="{PAD_L - 6}" y="{y + 4:.1f}" text-anchor="end">{_t(fmt(v, unit))}</text>'
        )
    body.append(f'<line class="ax" x1="{PAD_L}" x2="{W - PAD_R}" y1="{base}" y2="{base}"/>')
    for ci, cat in enumerate(categories):
        gx = PAD_L + ci * group_w + (group_w - bar_w * len(series)) / 2
        for si, (name, idx, vs) in enumerate(series):
            v = vs[ci]
            x = gx + si * bar_w
            if v is None:
                body.append(
                    f'<g><rect x="{x:.1f}" y="{base - 4}" width="{bar_w - 2:.1f}" height="4" class="na"/>'
                    f"<title>{_t(name)} · {_t(cat)}: unavailable</title></g>"
                )
                continue
            y = sy(v)
            body.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w - 2:.1f}" height="{max(base - y, 0.5):.1f}" '
                f'rx="2" style="fill:{color(idx)}"><title>{_t(name)} · {_t(cat)}: '
                f"{_t(fmt(v, unit))}</title></rect>"
            )
        label = cat if len(cat) <= 22 else cat[:21] + "…"
        cx = PAD_L + (ci + 0.5) * group_w
        body.append(
            f'<text class="tk" transform="translate({cx:.1f} {base + 14}) rotate(-20)" '
            f'text-anchor="end">{_t(label)}<title>{_t(cat)}</title></text>'
        )
    body.append(
        f'<text class="al" transform="translate(14 {(PAD_T + base) / 2:.1f}) rotate(-90)" text-anchor="middle">{_t(y_label)}</text>'
    )
    return _legend([(n, i) for n, i, _ in series]) + _svg(body, title, height=height)


def scatter(
    points: Sequence[tuple[str, int, float, float]],
    title: str,
    x_label: str,
    y_label: str,
    x_unit: str = "",
    y_unit: str = "",
    labeled: bool = True,
) -> str:
    """Points ``(label, color index, x, y)``. With ``labeled`` each point is named in text
    (model-level charts); otherwise points are anonymous requests of one model."""
    if not points:
        return _empty(title)
    xs = [p[2] for p in points]
    ys = [p[3] for p in points]
    x_ticks = nice_ticks(0, max(xs) or 1, 5)
    y_ticks = nice_ticks(0, 1.0 if y_unit == "ratio" else (max(ys) or 1), 4)
    xmax, ymax = x_ticks[-1] or 1, y_ticks[-1] or 1

    def sx(v: float) -> float:
        return PAD_L + v / xmax * (W - PAD_L - PAD_R)

    def sy(v: float) -> float:
        return PAD_T + (1 - v / ymax) * (H - PAD_T - PAD_B)

    body = _frame(x_ticks, y_ticks, sx, sy, x_label, y_label)
    r = 5 if labeled else 2.5
    for label, idx, x, y in points:
        body.append(
            f'<g><circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="{max(r, 8)}" class="hit"/>'
            f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="{r}" class="ring" style="fill:{color(idx)}"/>'
            f"<title>{_t(label)}: {_t(fmt(x, x_unit))}, {_t(fmt(y, y_unit))}</title></g>"
        )
        if labeled:
            anchor = "end" if sx(x) > W * 0.75 else "start"
            dx = -9 if anchor == "end" else 9
            body.append(
                f'<text class="pl" x="{sx(x) + dx:.1f}" y="{sy(y) + 4:.1f}" text-anchor="{anchor}">{_t(label)}</text>'
            )
    return _svg(body, title)
