"""Figure 1 (v8): false accusations by all five monitors on the release (F1) worlds, Studies 2 and 4.

Values (P3-original, repeat 0) come from:
- Study 2: experiments/e1-evidence-monitors/analysis/STUDY2_NUMBERS.md
  (Sonnet X 25/30, B 0/30; Haiku X 11/30, B 0/30; DeepSeek Flash X 30/30, B 5/30)
- Study 4: experiments/e1-evidence-monitors/analysis/STUDY4_NUMBERS.md
  (GPT-5.4-mini X 26/30; GPT-4.1-mini X 28/30; calibration B 0/18 each, on different seeds)
Same style as tools/make_figures.py (Astra's original Figure 1).
"""
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = pathlib.Path(__file__).resolve().parent.parent / "figures"
monitors = ["Claude\nSonnet 5.5", "Claude\nHaiku 5.5", "DeepSeek\nFlash", "GPT-5.4-mini", "GPT-4.1-mini"]
x_acc = [25, 11, 30, 26, 28]
x_n = [30, 30, 30, 30, 30]
b_acc = [0, 0, 5, 0, 0]
b_n = [30, 30, 30, 18, 18]  # GPT B rates are from Study 4 calibration (different seeds), not matched twins

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 11, "axes.labelsize": 10,
                     "legend.fontsize": 9, "pdf.fonttype": 42, "axes.spines.top": False, "axes.spines.right": False})
fig, ax = plt.subplots(figsize=(6.8, 2.6), layout="constrained")
x = np.arange(len(monitors))
w = 0.36
xb = ax.bar(x - w / 2, [a / n for a, n in zip(x_acc, x_n)], w, color="#275d7d", label="Exculpatory world (X)",
            edgecolor="white", linewidth=0.7, zorder=3)
bb = ax.bar(x + w / 2, [a / n for a, n in zip(b_acc, b_n)], w, color="#b36b30", hatch="///", label="Ordinary benign world (B)",
            edgecolor="white", linewidth=0.7, zorder=3)
for bar, a, n in zip(xb, x_acc, x_n):
    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02, f"{a}/{n}", ha="center", va="bottom", fontsize=9.5, fontweight="bold")
for i, (bar, a, n) in enumerate(zip(bb, b_acc, b_n)):
    lab = f"{a}/{n}"
    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02, lab, ha="center", va="bottom", fontsize=9.5)
ax.set_xticks(x, monitors)
ax.set_ylim(0, 1.18)
ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0], ["0%", "25%", "50%", "75%", "100%"])
ax.set_ylabel("Share falsely accused")
ax.axvline(2.5, color="#9aa5ab", linewidth=0.8, linestyle=":", zorder=1)
ax.text(1.0, 1.13, "Study 2", ha="center", fontsize=9, color="#555")
ax.text(3.5, 1.13, "Study 4 (prospective)", ha="center", fontsize=9, color="#555")
ax.grid(axis="y", color="#dce3e7", linewidth=0.7, zorder=0)
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), frameon=False, ncol=2, fontsize=9)
for ext in ("png", "pdf"):
    fig.savefig(OUT / f"release_all_monitors.{ext}", dpi=300)
print("wrote", OUT / "release_all_monitors.png")
