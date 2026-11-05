"""Charts for the reopen study, in the tape's look (tape/render.py).

Run after research/reopen.py: python research/reopen_charts.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matplotlib.ticker import PercentFormatter  # noqa: E402

from tape.render import EXCEL, Footer, _figure, _footer, _png, _style_axes  # noqa: E402

OUT = Path(__file__).resolve().parent / "reopen"
HANDLE = "@zetsurui"
SOURCE = "Hyperliquid (trade[XYZ]) prices; official opens via Yahoo Finance, FX-converted hourly"


def _footer_for(results: dict, note: str) -> Footer:
    day = datetime.fromisoformat(results["generated"]).strftime("%Y-%m-%d")
    return Footer(handle=HANDLE, date_text=day, note=note)


def share_chart(results: dict) -> bytes:
    """How much of each opening gap Hyperliquid had priced before the open,
    next to the best externally priced stand-in over the same hours."""
    order = [
        ("xyz:SKHX", "SK hynix\n(Seoul)"),
        ("xyz:SMSN", "Samsung\n(Seoul)"),
        ("xyz:CXMT", "CXMT\n(Shanghai)"),
        ("xyz:KIOXIA", "Kioxia\n(Tokyo)"),
        ("xyz:MINIMAX", "MiniMax\n(Hong Kong)"),
        ("xyz:ZHIPU", "Zhipu\n(Hong Kong)"),
    ]
    fig = _figure(1200, 675)
    ax = fig.add_subplot(111)
    fig.subplots_adjust(left=0.07, right=0.97, top=0.84, bottom=0.22)
    fig.suptitle(
        "While Asia sleeps, Hyperliquid prices the next open",
        x=0.07,
        y=0.965,
        ha="left",
        fontsize=15,
    )
    ax.set_title(
        "Share of the opening gap priced on Hyperliquid before the home market opened (vs. assuming no move)",
        loc="left",
        fontsize=10.5,
        color="#404040",
    )
    _style_axes(ax, "pct")
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    xs = range(len(order))
    w = 0.38
    for i, (coin, _label) in enumerate(order):
        r = results["asia"][coin]
        b = r.get("baseline") or {}
        hl = b.get("hl_share_same_days", r.get("share_priced"))
        ax.bar(i - w / 2, hl, w, color=EXCEL[0], edgecolor="#404040", lw=0.5, label="Hyperliquid" if i == 0 else None)
        ax.text(i - w / 2, hl + 0.015, f"{hl:.0%}", ha="center", fontsize=10)
        if b.get("proxy_share") is not None:
            p = b["proxy_share"]
            ax.bar(
                i + w / 2,
                max(p, 0),
                w,
                color="#BFBFBF",
                edgecolor="#404040",
                lw=0.5,
                label="Best US-listed stand-in" if i == 0 else None,
            )
            ax.text(i + w / 2, max(p, 0) + 0.015, f"{p:.0%}\n{r['proxy']}", ha="center", fontsize=8, color="#404040")
        else:
            ax.text(i + w / 2, 0.02, "no\nstand-in", ha="center", fontsize=8, color="#7F7F7F")
    labels = []
    for coin, lbl in order:
        r = results["asia"][coin]
        n = (r.get("baseline") or {}).get("n") or r.get("n")
        labels.append(f"{lbl}\n{n} nights")
    ax.set_xticks(list(xs), labels, fontsize=10)
    ax.set_ylim(0, 1)
    ax.legend(loc="upper right", frameon=True, fancybox=False, edgecolor="#7F7F7F", fontsize=10)
    first = min(r["from"] for r in results["asia"].values() if "from" in r)
    _footer(fig, _footer_for(results, f"{SOURCE}. Closures {first} to {results['generated'][:10]}."))
    return _png(fig)


def scatter_chart(windows: list[dict], results: dict) -> bytes:
    """SK hynix and Samsung: Hyperliquid's overnight move against the KRX open."""
    fig = _figure(1200, 675)
    ax = fig.add_subplot(111)
    fig.subplots_adjust(left=0.08, right=0.97, top=0.84, bottom=0.13)
    fig.suptitle(
        "Seoul's opening gap, called overnight on Hyperliquid",
        x=0.08,
        y=0.965,
        ha="left",
        fontsize=15,
    )
    ax.set_title(
        "Each dot is one night: Hyperliquid's move while KRX was shut (x) against KRX's opening gap (y)",
        loc="left",
        fontsize=10.5,
        color="#404040",
    )
    _style_axes(ax, "pct")
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    lim = 0
    for coin, label, color in (("xyz:SKHX", "SK hynix", EXCEL[0]), ("xyz:SMSN", "Samsung", EXCEL[1])):
        pts = [(w["m"], w["g"]) for w in windows if w["market"] == coin]
        r = results["asia"][coin]
        ax.scatter(
            [m for m, _ in pts],
            [g for _, g in pts],
            s=22,
            color=color,
            edgecolor="#404040",
            lw=0.3,
            label=f"{label}: {r['share_priced']:.0%} of the gap priced, direction right on {r['hit_rate_big_gaps']:.0%} of gaps over 1%",
        )
        lim = max([lim] + [abs(v) for p in pts for v in p])
    lim *= 1.08
    ax.plot([-lim, lim], [-lim, lim], color="#7F7F7F", lw=1, ls="--", label="gap = Hyperliquid's move")
    ax.axhline(0, color="#7F7F7F", lw=0.8)
    ax.axvline(0, color="#7F7F7F", lw=0.8)
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    top = max((w for w in windows if w["market"] in ("xyz:SKHX", "xyz:SMSN")), key=lambda w: abs(w["g"]))
    ax.annotate(
        datetime.fromisoformat(top["day"]).strftime("%-d %b"),
        xy=(top["m"], top["g"]),
        xytext=(-60, 0),
        textcoords="offset points",
        fontsize=9,
        color="#404040",
        arrowprops=dict(arrowstyle="-", color="#7F7F7F", lw=0.8),
    )
    ax.set_xlabel("Hyperliquid's move, last Korean print (20:00 KST) to 08:00 KST")
    ax.set_ylabel("KRX opening gap, in USD")
    ax.legend(loc="upper left", frameon=True, fancybox=False, edgecolor="#7F7F7F", fontsize=9.5)
    _footer(fig, _footer_for(results, SOURCE + "."))
    return _png(fig)


def main() -> None:
    results = json.loads((OUT / "results.json").read_text())
    windows = json.loads((OUT / "windows.json").read_text())
    (OUT / "share.png").write_bytes(share_chart(results))
    (OUT / "seoul.png").write_bytes(scatter_chart(windows, results))
    print("wrote", OUT / "share.png", OUT / "seoul.png")


if __name__ == "__main__":
    main()
