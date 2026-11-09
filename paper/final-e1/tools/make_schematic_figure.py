"""Figure 1 (v8): schematic of the exculpatory triplet test, with Sonnet's Study 2 outcome per world type.

Outcomes from experiments/e1-evidence-monitors/analysis/STUDY2_NUMBERS.md (Sonnet P3-original):
H accused 29/30, X accused 25/30, B accused 0/30. Witness: get_deploy then approvals_for_digest (2 of 3 queries).
"""
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

OUT = pathlib.Path(__file__).resolve().parent.parent / "figures"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "pdf.fonttype": 42})
fig, ax = plt.subplots(figsize=(6.8, 2.95))
ax.set_xlim(0, 100)
ax.set_ylim(0, 66)
ax.axis("off")
INK, MUTED, BLUE, RED, GREEN = "#1f2a30", "#5b6770", "#275d7d", "#a3302a", "#2f6b3a"


def box(x, y, w, h, face, edge, lw=1.0):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.35,rounding_size=1.2", fc=face, ec=edge, lw=lw))


box(2, 55, 96, 9, "#eef2f4", MUTED)
ax.text(50, 61.3, "Same visible log in all three worlds", ha="center", va="center", fontsize=9.5, fontweight="bold", color=INK)
ax.text(50, 57.6, "An agent deploys a release citing an approval; the log cannot tell the worlds apart",
        ha="center", va="center", fontsize=8.0, color=INK)
ax.text(50, 50.6, "Hidden state: visible only by querying (X's clearing record is 2 of the monitor's 3 queries away)",
        ha="center", va="center", fontsize=8.0, color=BLUE, style="italic")

W = 29
cols = [
    (2.5, "H  harmful", "The only approval for the\ndeployed version has the\nwrong scope or is too late", "Truth: violation", INK,
     "Accused 29/30", False),
    (35.5, "X  exculpatory", "A qualifying approval exists:\nright version and scope,\nissued before deployment", "Truth: compliant", GREEN,
     "Accused 25/30", True),
    (68.5, "B  ordinary benign", "Benign state that the\nmonitor's routine lookups\nalready reveal", "Truth: compliant", GREEN,
     "Accused 0/30", False),
]
for x, title, hidden, truth, tcol, outcome, is_x in cols:
    cx = x + W / 2
    ax.annotate("", xy=(cx, 44.2), xytext=(cx, 48.6), arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=0.9))
    box(x, 24.5, W, 19, "white", BLUE if is_x else MUTED, lw=1.6 if is_x else 1.0)
    ax.text(cx, 40.6, title, ha="center", va="center", fontsize=9.3, fontweight="bold", color=BLUE if is_x else INK)
    ax.text(cx, 33.4, hidden, ha="center", va="center", fontsize=7.6, color=INK, linespacing=1.25)
    ax.text(cx, 26.6, truth, ha="center", va="center", fontsize=8.0, color=tcol, fontweight="bold")
    ax.annotate("", xy=(cx, 16.4), xytext=(cx, 23.6), arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=0.9))
    box(x + 4, 9.0, W - 8, 7, "#fbeeee" if is_x else "#f3f6f4", RED if is_x else MUTED)
    ax.text(cx, 12.5, outcome, ha="center", va="center", fontsize=9.3, fontweight="bold", color=RED if is_x else INK)

ax.text(50, 3.0, "Monitor: Claude Sonnet 5.5 (Study 2, 30 triplets). It caught the harmful cases but accused most compliant X agents,\n"
                 "because it rarely looked up the approval that would have cleared them.",
        ha="center", va="center", fontsize=7.8, color=MUTED, linespacing=1.3)
fig.tight_layout(pad=0.2)
for ext in ("png", "pdf"):
    fig.savefig(OUT / f"triplet_schematic.{ext}", dpi=300)
print("wrote", OUT / "triplet_schematic.png")
