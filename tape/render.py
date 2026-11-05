"""Charts. One function per chart type; inputs are plain data, output is PNG bytes.

The look matches the site (a quiet 2000s broadcast caption): a cool grey
ground, hairline gridlines, a heavy gothic title (Zen Kaku Gothic New Black),
figures in BIZ UDGothic, navy for the headline series and a short palette
for the rest. Green and red are kept for long and short callouts. No logo,
no gradient. The site serves these same PNGs, so a screenshot of the site
and a post are the same picture. The fonts ship in tape/fonts (SIL OFL),
subset to Latin, so a render never depends on what the box has.
"""

from __future__ import annotations

import io
import math
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.dates import AutoDateLocator, DateFormatter  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.ticker import FuncFormatter, PercentFormatter  # noqa: E402
from PIL import Image  # noqa: E402

# The site's tokens (site/app/globals.css). Keep the two in step.
INK = "#F3F4F7"  # the ground
TEXT = "#121733"
DIM = "#4C5270"
GRID = "#D7DAE3"
AXIS = "#9DA2B5"
# Series colors, in legend order. None of them carries a meaning on the site:
# green (long), red (short, callouts) and the link blue are kept out.
PALETTE = ["#2F6FE0", "#E08A00", "#C2185B", "#7A4FD0", "#0E7C86", "#8C5A2B", "#6B7280", "#B08900"]
HEAVY = TEXT
LONG = "#1E8E3E"
SHORT = "#D3202A"
CALLOUT = SHORT

_FONTS = Path(__file__).parent / "fonts"
for _f in _FONTS.glob("*.ttf"):
    font_manager.fontManager.addfont(str(_f))
TITLE_FONT = font_manager.FontProperties(fname=str(_FONTS / "ZenKakuGothicNew-Black.ttf"))
matplotlib.rcParams.update(
    {
        "font.family": ["BIZ UDGothic", "DejaVu Sans"],
        "text.color": TEXT,
        "axes.labelcolor": DIM,
        "axes.edgecolor": AXIS,
        "axes.facecolor": INK,
        "xtick.color": DIM,
        "ytick.color": DIM,
        "xtick.labelcolor": DIM,
        "ytick.labelcolor": DIM,
        "legend.labelcolor": TEXT,
        "axes.unicode_minus": True,
    }
)


@dataclass
class Line:
    label: str
    points: Sequence[tuple[datetime, float | None, float | None]]  # (hour, value, coverage)
    color: str = PALETTE[0]
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
    value: float | None = None  # signed: green when positive, orange otherwise, as on the site


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
    """Pixel size of the PNG. `scale` above 1 draws a phone chart: the same
    pixels on a smaller canvas, so every font and line comes out `scale` times
    larger, and the layouts switch to a portrait arrangement."""

    width: int = 1200
    height: int = 675
    scale: float = 1.0
    extra: dict = field(default_factory=dict)

    @property
    def phone(self) -> bool:
        return self.scale > 1


# Phone versions, served to narrow screens. 1080 px wide at 2x: a 1200 px
# chart shown at phone width sets 10 pt labels at about 3 px; these at about 9.
PHONE_CARD = ChartSize(1080, 1350, scale=2.0)
PHONE_GRID = ChartSize(1080, 1500, scale=2.0)
PHONE_PANELS = ChartSize(1080, 1900, scale=2.0)


def _figure(size: ChartSize) -> Figure:
    k = size.scale
    fig = Figure(figsize=(size.width / (100 * k), size.height / (100 * k)), dpi=100 * k, facecolor=INK)
    FigureCanvasAgg(fig)
    return fig


def _wrap(text: str, size: ChartSize, width: int = 36) -> str:
    return textwrap.fill(text, width) if size.phone else text


def _png(fig: Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=INK)
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
        return FuncFormatter(lambda v, _: f"${v / 1e6:.0f}M")
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
    ax.set_facecolor(INK)
    ax.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    f = _formatter(fmt)
    if f:
        ax.yaxis.set_major_formatter(f)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(length=0, pad=6)


def _title(ax, text: str, size: float) -> None:
    ax.set_title(text, loc="left", fontproperties=TITLE_FONT, fontsize=size, color=TEXT, pad=10)


def _suptitle(fig: Figure, text: str, x: float, y: float, size: float) -> None:
    fig.text(x, y, text, ha="left", va="top", fontproperties=TITLE_FONT, fontsize=size, color=TEXT)


def _legend(ax, **kw) -> None:
    leg = ax.legend(frameon=True, fancybox=False, edgecolor=GRID, facecolor=INK, framealpha=1, **kw)
    leg.get_frame().set_linewidth(0.8)


def _zero(ax) -> None:
    ax.axhline(0, color=AXIS, lw=1)


def _clip(xlim: tuple[datetime, datetime] | None, xs: list[datetime]) -> tuple[datetime, datetime] | None:
    """Start the window at the first data point when history is shorter than it."""
    if not xlim or not xs:
        return xlim
    return (max(xlim[0], min(xs)), xlim[1])


def _date_axis(ax, span_days: float, xlim: tuple[datetime, datetime] | None = None, maxticks: int = 10) -> None:
    if xlim:
        ax.set_xlim(*xlim)
        span_days = (xlim[1] - xlim[0]).total_seconds() / 86400
    ax.xaxis.set_major_locator(AutoDateLocator(minticks=min(3, maxticks - 1), maxticks=maxticks))
    ax.xaxis.set_major_formatter(DateFormatter("%b %d" if span_days > 2 else "%H:%M"))


def _footer(fig: Figure, footer: Footer, size: ChartSize | None = None) -> None:
    fig.text(0.99, 0.014, f"{footer.handle}  {footer.date_text}", ha="right", va="bottom", fontsize=11, color=TEXT)
    if footer.note:
        # On a phone chart the note gets its own line above the handle.
        y = 0.014 + (0.022 if size is not None and size.phone else 0)
        fig.text(0.01, y, footer.note, ha="left", va="bottom", fontsize=9, color=DIM)


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
    phone = size.phone
    fig = _figure(size)
    # Phone: title across the top, legend under it, the plot full width.
    box = (
        dict(left=0.14, right=0.94, top=0.79, bottom=0.10)
        if phone
        else dict(left=0.07, right=0.80, top=0.91, bottom=0.10)
    )
    if bars:
        gs = fig.add_gridspec(2, 1, height_ratios=[3.2, 1.3] if phone else [4, 1.1], hspace=0.4, **box)
        ax = fig.add_subplot(gs[0])
        axb = fig.add_subplot(gs[1])
    else:
        ax = fig.add_subplot(111)
        fig.subplots_adjust(**box)
        axb = None
    if phone:
        _suptitle(fig, _wrap(title, size), 0.04, 0.975, 14)
    else:
        _title(ax, title, 15)
    _style_axes(ax, "pct")
    _zero(ax)
    span = 1.0
    all_x = [p[0] for ln in lines for p in ln.points]
    if all_x:
        span = (max(all_x) - min(all_x)).total_seconds() / 86400
    for ln in lines:
        _plot_line(ax, ln, dashed_below, 3.2 if ln.heavy else 1.6)
    _date_axis(ax, span, _clip(xlim, all_x), maxticks=5 if phone else 10)
    if phone:
        _legend(ax, loc="lower left", bbox_to_anchor=(-0.02, 1.03), fontsize=8.5, ncol=3, borderaxespad=0)
    else:
        _legend(ax, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=10)

    if callout:
        target = next((ln for ln in lines if ln.label == callout.line_label), None)
        lp = _last_point(target) if target else None
        if lp:
            t, v = lp
            cc = LONG if (callout.value or 0) > 0 else SHORT
            ax.plot([t], [v], marker="o", ms=20, mfc="none", mec=cc, mew=2)
            ylo, yhi = ax.get_ylim()
            dy = (yhi - ylo) * (0.18 if v < (ylo + yhi) / 2 else -0.18)
            ax.annotate(
                callout.text,
                xy=(t, v),
                xytext=(-120 if phone else -150, 50 if dy > 0 else -50),
                textcoords="offset points",
                color=cc,
                fontsize=11 if phone else 13,
                fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=cc, lw=2, connectionstyle="arc3,rad=-0.35"),
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
        axb.bar([b.label for b in bars], vals, color=[b.color for b in bars], width=0.6)
        _zero(axb)
        _style_axes(axb, "pct")
        axb.set_title(bars_title, loc="left", fontsize=10, color=DIM)
        axb.tick_params(axis="x", labelsize=8 if phone else 9)
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
                    color=TEXT,
                )
    _footer(fig, footer, size)
    return _png(fig)


def render_panels(
    *,
    title: str,
    panels: list[Panel],
    footer: Footer,
    dashed_below: float,
    size: ChartSize | None = None,
    xlim: tuple[datetime, datetime] | None = None,
) -> bytes:
    """A market page chart: stacked panels sharing the time axis."""
    size = size or ChartSize(1200, 1000)
    phone = size.phone
    fig = _figure(size)
    n = len(panels)
    axes = fig.subplots(n, 1, sharex=True)
    if n == 1:
        axes = [axes]
    if phone:
        fig.subplots_adjust(left=0.15, right=0.94, top=0.9, bottom=0.07, hspace=0.8)
        _suptitle(fig, _wrap(title, size, 30), 0.04, 0.985, 14)
    else:
        fig.subplots_adjust(left=0.08, right=0.97, top=0.9, bottom=0.06, hspace=0.55)
        _suptitle(fig, title, 0.08, 0.975, 16)
    span = 1.0
    for ax, p in zip(axes, panels, strict=True):
        _style_axes(ax, p.fmt)
        if p.zero_line:
            _zero(ax)
        for ln in p.lines:
            _plot_line(ax, ln, dashed_below, 1.8)
            if ln.points:
                span = max(span, (ln.points[-1][0] - ln.points[0][0]).total_seconds() / 86400)
        ax.set_title(p.title, loc="left", fontsize=10 if phone else 11, color=TEXT)
        if len(p.lines) > 1:
            if phone:  # beside the panel title, clear of the data
                _legend(
                    ax, loc="lower right", bbox_to_anchor=(1.0, 1.0), fontsize=8, ncol=len(p.lines), borderaxespad=0.2
                )
            else:
                _legend(ax, loc="upper left", fontsize=8, ncol=len(p.lines))
    all_x = [pt[0] for p in panels for ln in p.lines for pt in ln.points]
    _date_axis(axes[-1], span, _clip(xlim, all_x), maxticks=4 if phone else 10)
    _footer(fig, footer, size)
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
    phone = size.phone
    if phone:
        cols = 2
    fig = _figure(size)
    rows = max(1, math.ceil(len(panels) / cols))
    axes = fig.subplots(rows, cols, sharex=True, sharey=True, squeeze=False)
    if phone:
        fig.subplots_adjust(left=0.14, right=0.95, top=0.88, bottom=0.08, hspace=0.65, wspace=0.15)
        _suptitle(fig, _wrap(title, size, 30), 0.04, 0.98, 14)
    else:
        fig.subplots_adjust(left=0.07, right=0.98, top=0.86, bottom=0.12, hspace=0.5, wspace=0.12)
        _suptitle(fig, title, 0.07, 0.965, 16)
    span = 1.0
    for i, ax in enumerate(axes.flat):
        if i >= len(panels):
            ax.set_visible(False)
            continue
        label, ln = panels[i]
        _style_axes(ax, "pct")
        _zero(ax)
        _plot_line(ax, ln, dashed_below, 1.8)
        lp = _last_point(ln)
        ax.set_title(
            f"{label}  {lp[1] * 100:+.1f}%" if lp else f"{label}  no data", loc="left", fontsize=10, color=TEXT
        )
        if ln.points:
            span = max(span, (ln.points[-1][0] - ln.points[0][0]).total_seconds() / 86400)
        ax.tick_params(labelsize=8)
    # Hidden slots in the last row leave the panel above them without a date
    # axis; give the bottom visible panel of every column its labels.
    for i, ax in enumerate(axes.flat):
        if i < len(panels) and i + cols >= len(panels):
            ax.xaxis.set_tick_params(labelbottom=True)
    all_x = [pt[0] for _, ln in panels for pt in ln.points]
    _date_axis(axes.flat[0], span, _clip(xlim, all_x), maxticks=3 if phone else 4)
    _footer(fig, footer, size)
    return _png(fig)


def render_notice(*, text: str, footer: Footer, size: ChartSize | None = None) -> bytes:
    """A plain card for when there is no chart to show (gap day, no data yet)."""
    size = size or ChartSize()
    fig = _figure(size)
    fig.text(0.05, 0.6, text, fontproperties=TITLE_FONT, fontsize=26, va="center", wrap=True, color=TEXT)
    _footer(fig, footer, size)
    return _png(fig)
