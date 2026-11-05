"""Charts. One function per chart type; inputs are plain data, output is PNG bytes.

The look is deliberately crude: default matplotlib, the default-Excel palette,
gridlines, a framed legend, the handle and date in the bottom-right corner and
nothing else. No logo, no gradient, no dark mode. The site serves these same
PNGs, so a screenshot of the site and a post are the same picture.
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.dates import AutoDateLocator, DateFormatter  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.ticker import FuncFormatter, PercentFormatter  # noqa: E402
from PIL import Image  # noqa: E402

# Excel 2007-2013 default series colors.
EXCEL = ["#4F81BD", "#C0504D", "#9BBB59", "#8064A2", "#4BACC6", "#F79646", "#2C4D75", "#772C2A"]
HEAVY = "#000000"
CALLOUT = "#FF0000"


@dataclass
class Line:
    label: str
    points: Sequence[tuple[datetime, float | None, float | None]]  # (hour, value, coverage)
    color: str = EXCEL[0]
    heavy: bool = False


@dataclass
class Bar:
    label: str
    value: float | None
    color: str


@dataclass
class Callout:
    line_label: str
    text: str


@dataclass
class Marker:
    time: datetime
    text: str


@dataclass
class Panel:
    title: str
    lines: list[Line]
    fmt: str = "pct"  # pct | bps | pct_ann
    zero_line: bool = True


@dataclass
class Footer:
    handle: str
    date_text: str
    note: str | None = None  # e.g. "coverage 96.1%: dashed below 98%"


@dataclass
class ChartSize:
    width: int = 1200
    height: int = 675
    extra: dict = field(default_factory=dict)


def _figure(w: int, h: int) -> Figure:
    fig = Figure(figsize=(w / 100, h / 100), dpi=100, facecolor="white")
    FigureCanvasAgg(fig)
    return fig


def _png(fig: Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    buf.seek(0)
    im = Image.open(buf).convert("RGB").quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _formatter(fmt: str):
    if fmt == "pct":
        return PercentFormatter(1.0, decimals=None)
    if fmt == "bps":
        return FuncFormatter(lambda v, _: f"{v:.0f} bp")
    if fmt == "pct_ann":
        return FuncFormatter(lambda v, _: f"{v * 100:.0f}%")
    if fmt == "usd":
        return FuncFormatter(
            lambda v, _: (
                "$0"
                if v == 0
                else f"${v / 1e6:.0f}M"
                if abs(v) >= 1e7
                else f"${v / 1e6:.1f}M"
                if abs(v) >= 1e5
                else f"${v / 1e3:.0f}k"
            )
        )
    return None


def _plot_line(ax, line: Line, dashed_below: float, lw: float) -> None:
    xs = [p[0] for p in line.points]
    ys = [math.nan if p[1] is None else p[1] for p in line.points]
    if not xs:
        return
    solid = [
        y if (c is not None and c >= dashed_below) else math.nan for (_, _, c), y in zip(line.points, ys, strict=True)
    ]
    # The dashed line runs under everything; solid covers the hours with enough coverage.
    marker = "o" if len(xs) <= 3 else None  # a line needs two points; show the first hours as dots
    ax.plot(xs, ys, color=line.color, lw=lw * 0.8, ls="--", marker=marker, ms=4)
    ax.plot(xs, solid, color=line.color, lw=lw, label=line.label, marker=marker, ms=5)


def _last_point(line: Line) -> tuple[datetime, float] | None:
    for t, v, _ in reversed(line.points):
        if v is not None:
            return t, v
    return None


def _style_axes(ax, fmt: str) -> None:
    ax.grid(True, color="#BFBFBF", lw=0.8)
    ax.set_axisbelow(True)
    f = _formatter(fmt)
    if f:
        ax.yaxis.set_major_formatter(f)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def _date_axis(ax, span_days: float, xlim: tuple[datetime, datetime] | None = None) -> None:
    if xlim:
        ax.set_xlim(*xlim)
        span_days = (xlim[1] - xlim[0]).total_seconds() / 86400
    ax.xaxis.set_major_locator(AutoDateLocator(minticks=4, maxticks=10))
    ax.xaxis.set_major_formatter(DateFormatter("%b %d" if span_days > 2 else "%H:%M"))


def _footer(fig: Figure, footer: Footer) -> None:
    fig.text(0.99, 0.012, f"{footer.handle}  {footer.date_text}", ha="right", va="bottom", fontsize=11)
    if footer.note:
        fig.text(0.01, 0.012, footer.note, ha="left", va="bottom", fontsize=9, color="#595959")


def render_card(
    *,
    title: str,
    lines: list[Line],
    bars: list[Bar] | None,
    bars_title: str,
    callout: Callout | None,
    marker: Marker | None,
    footer: Footer,
    dashed_below: float,
    size: ChartSize | None = None,
    xlim: tuple[datetime, datetime] | None = None,
) -> bytes:
    """One panel of lines (whale net as a share of OI), an optional bar strip
    under it (the day's change per line), and an optional hand-drawn callout."""
    size = size or ChartSize()
    fig = _figure(size.width, size.height)
    if bars:
        gs = fig.add_gridspec(2, 1, height_ratios=[4, 1.1], hspace=0.35, left=0.07, right=0.80, top=0.91, bottom=0.10)
        ax = fig.add_subplot(gs[0])
        axb = fig.add_subplot(gs[1])
    else:
        ax = fig.add_subplot(111)
        fig.subplots_adjust(left=0.07, right=0.80, top=0.91, bottom=0.10)
        axb = None
    ax.set_title(title, loc="left", fontsize=13)
    _style_axes(ax, "pct")
    ax.axhline(0, color="#7F7F7F", lw=1)
    span = 1.0
    all_x = [p[0] for ln in lines for p in ln.points]
    if all_x:
        span = (max(all_x) - min(all_x)).total_seconds() / 86400
    for ln in lines:
        _plot_line(ax, ln, dashed_below, 3.2 if ln.heavy else 1.6)
    _date_axis(ax, span, xlim)
    ax.legend(
        loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=True, fontsize=10, edgecolor="#7F7F7F", fancybox=False
    )

    if callout:
        target = next((ln for ln in lines if ln.label == callout.line_label), None)
        lp = _last_point(target) if target else None
        if lp:
            t, v = lp
            ax.plot([t], [v], marker="o", ms=20, mfc="none", mec=CALLOUT, mew=2)
            ylo, yhi = ax.get_ylim()
            dy = (yhi - ylo) * (0.18 if v < (ylo + yhi) / 2 else -0.18)
            ax.annotate(
                callout.text,
                xy=(t, v),
                xytext=(-150, 60 if dy > 0 else -60),
                textcoords="offset points",
                color=CALLOUT,
                fontsize=13,
                fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=CALLOUT, lw=2, connectionstyle="arc3,rad=-0.35"),
            )

    if marker:
        ax.axvline(marker.time, color=CALLOUT, lw=1.5, ls=":")
        ax.annotate(
            marker.text,
            xy=(marker.time, ax.get_ylim()[1]),
            xytext=(-8, -16),
            textcoords="offset points",
            ha="right",
            color=CALLOUT,
            fontsize=11,
            fontweight="bold",
        )

    if axb is not None and bars:
        vals = [0.0 if b.value is None else b.value for b in bars]
        axb.bar([b.label for b in bars], vals, color=[b.color for b in bars], edgecolor="#404040", lw=0.5)
        axb.axhline(0, color="#7F7F7F", lw=1)
        axb.set_title(bars_title, loc="left", fontsize=10)
        _style_axes(axb, "pct")
        axb.tick_params(axis="x", labelsize=9)
        lim = max([abs(v) for v in vals] + [0.001]) * 1.5
        axb.set_ylim(-lim, lim)
        for i, b in enumerate(bars):
            if b.value is not None:
                axb.text(
                    i,
                    vals[i],
                    f"{b.value * 100:+.1f}pp",
                    ha="center",
                    va="bottom" if vals[i] >= 0 else "top",
                    fontsize=9,
                )
    _footer(fig, footer)
    return _png(fig)


def render_panels(
    *,
    title: str,
    panels: list[Panel],
    footer: Footer,
    dashed_below: float,
    size: ChartSize | None = None,
    xlim: tuple[datetime, datetime] | None = None,
    marker: Marker | None = None,
) -> bytes:
    """A market page chart: stacked panels sharing the time axis."""
    size = size or ChartSize(1200, 1000)
    fig = _figure(size.width, size.height)
    n = len(panels)
    axes = fig.subplots(n, 1, sharex=True)
    if n == 1:
        axes = [axes]
    fig.subplots_adjust(left=0.08, right=0.97, top=0.91, bottom=0.06, hspace=0.5)
    fig.suptitle(title, x=0.08, y=0.985, ha="left", fontsize=14)
    span = 1.0
    for ax, p in zip(axes, panels, strict=True):
        _style_axes(ax, p.fmt)
        if p.zero_line:
            ax.axhline(0, color="#7F7F7F", lw=1)
        for ln in p.lines:
            _plot_line(ax, ln, dashed_below, 1.8)
            if ln.points:
                span = max(span, (ln.points[-1][0] - ln.points[0][0]).total_seconds() / 86400)
        ax.set_title(p.title, loc="left", fontsize=11)
        if marker:
            ax.axvline(marker.time, color=CALLOUT, lw=1.5, ls=":")
        if len(p.lines) > 1:
            ax.legend(
                loc="upper left", fontsize=8, frameon=True, fancybox=False, edgecolor="#7F7F7F", ncol=len(p.lines)
            )
    _date_axis(axes[-1], span, xlim)
    _footer(fig, footer)
    return _png(fig)


def render_grid(
    *,
    title: str,
    panels: list[tuple[str, Line]],
    footer: Footer,
    dashed_below: float,
    size: ChartSize | None = None,
    cols: int = 4,
    xlim: tuple[datetime, datetime] | None = None,
) -> bytes:
    """Small multiples, one panel per leg, same y scale."""
    size = size or ChartSize()
    fig = _figure(size.width, size.height)
    rows = max(1, math.ceil(len(panels) / cols))
    axes = fig.subplots(rows, cols, sharex=True, sharey=True, squeeze=False)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.88, bottom=0.12, hspace=0.45, wspace=0.12)
    fig.suptitle(title, x=0.07, ha="left", fontsize=13)
    span = 1.0
    for i, ax in enumerate(axes.flat):
        if i >= len(panels):
            ax.set_visible(False)
            continue
        label, ln = panels[i]
        _style_axes(ax, "pct")
        ax.axhline(0, color="#7F7F7F", lw=1)
        _plot_line(ax, ln, dashed_below, 1.8)
        lp = _last_point(ln)
        ax.set_title(f"{label}  {lp[1] * 100:+.1f}%" if lp else f"{label}  no data", loc="left", fontsize=10)
        if ln.points:
            span = max(span, (ln.points[-1][0] - ln.points[0][0]).total_seconds() / 86400)
        ax.tick_params(labelsize=8)
    _date_axis(axes.flat[0], span, xlim)
    _footer(fig, footer)
    return _png(fig)


def render_notice(*, text: str, footer: Footer, size: ChartSize | None = None) -> bytes:
    """A plain card for when there is no chart to show (gap day, no data yet)."""
    size = size or ChartSize()
    fig = _figure(size.width, size.height)
    fig.text(0.05, 0.6, text, fontsize=20, va="center", wrap=True)
    _footer(fig, footer)
    return _png(fig)


@dataclass
class HBar:
    label: str
    value: float
    lo: float | None = None
    hi: float | None = None
    note: str = ""


def render_bars(*, title: str, bars: list[HBar], footer: Footer, xlabel: str, size: ChartSize | None = None) -> bytes:
    """Horizontal bars in USD, largest first, with a lo-hi whisker where there is one."""
    size = size or ChartSize()
    fig = _figure(size.width, size.height)
    ax = fig.add_subplot(111)
    fig.subplots_adjust(left=0.30, right=0.90, top=0.90, bottom=0.12)
    ax.set_title(title, loc="left", fontsize=13)
    ys = list(range(len(bars)))[::-1]
    vals = [b.value for b in bars]
    ax.barh(ys, vals, color=EXCEL[0], edgecolor="#404040", lw=0.5)
    for y, b in zip(ys, bars, strict=True):
        if b.lo is not None and b.hi is not None:
            ax.plot([b.lo, b.hi], [y, y], color=HEAVY, lw=1.5)
            ax.plot([b.lo, b.lo], [y - 0.15, y + 0.15], color=HEAVY, lw=1.5)
            ax.plot([b.hi, b.hi], [y - 0.15, y + 0.15], color=HEAVY, lw=1.5)
        right = max(b.value, b.hi or 0)
        amount = f"${b.value / 1e6:,.1f}M" if b.value >= 1e5 else f"${b.value / 1e3:,.0f}k"
        ax.text(right, y, f"  {amount}{(' ' + b.note) if b.note else ''}", va="center", fontsize=9)
    ax.set_yticks(ys)
    ax.set_yticklabels([b.label for b in bars], fontsize=10)
    ax.grid(True, axis="x", color="#BFBFBF", lw=0.8)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_formatter(_formatter("usd"))
    ax.set_xlabel(xlabel, fontsize=10)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    hi = max([max(b.value, b.hi or 0) for b in bars] + [1.0])
    ax.set_xlim(0, hi * 1.25)
    _footer(fig, footer)
    return _png(fig)


def render_rounds(
    *,
    title: str,
    paths: dict[str, list[float]],
    forced: dict[str, list[float]],
    footer: Footer,
    size: ChartSize | None = None,
) -> bytes:
    """A cascade by round: each market's price (change from the sweep mark) on
    top, forced notional by round as stacked bars under it. Round 0 is the shock."""
    size = size or ChartSize()
    fig = _figure(size.width, size.height)
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 2], hspace=0.35, left=0.08, right=0.80, top=0.91, bottom=0.10)
    ax, axb = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
    ax.set_title(title, loc="left", fontsize=13)
    _style_axes(ax, "pct")
    ax.axhline(0, color="#7F7F7F", lw=1)
    for i, (label, pts) in enumerate(paths.items()):
        ax.plot(range(len(pts)), pts, color=EXCEL[i % len(EXCEL)], lw=2, marker="o", ms=4, label=label)
    ax.legend(
        loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=True, fontsize=9, edgecolor="#7F7F7F", fancybox=False
    )
    ax.set_xlabel("round", fontsize=9)
    n = max([len(v) for v in forced.values()] + [1])
    bottom = [0.0] * n
    for i, (label, vals) in enumerate(forced.items()):
        vals = list(vals) + [0.0] * (n - len(vals))
        axb.bar(
            range(1, n + 1), vals, bottom=bottom, color=EXCEL[i % len(EXCEL)], edgecolor="#404040", lw=0.5, label=label
        )
        bottom = [b + v for b, v in zip(bottom, vals, strict=True)]
    axb.set_title("Forced selling by round", loc="left", fontsize=10)
    _style_axes(axb, "usd")
    axb.set_xlabel("round", fontsize=9)
    ax.set_xticks(range(n + 1))
    axb.set_xticks(range(1, n + 1))
    axb.set_xlim(0.4, n + 0.6)
    _footer(fig, footer)
    return _png(fig)
