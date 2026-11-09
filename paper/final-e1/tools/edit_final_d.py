"""Final-d: replace the artifact-zip mention with the GitHub repo link; credit John Kitaoka's talk and MiniLab."""
import pathlib

p = pathlib.Path(__file__).resolve().parent.parent / "manuscript-final.md"
s = p.read_text(encoding="utf-8")
REPO = "https://github.com/ebt55/one-query-away"


def rep(a, b):
    global s
    assert a in s, a[:90]
    s = s.replace(a, b, 1)


rep("All code and data were created during this sprint. The accompanying artifact (E1-REPORTED-STUDIES-ARTIFACT-20261109.zip) contains the generators, synthetic states, protocols, raw model and query records, analyses and audit receipts, with offline commands that reconstruct Studies 2 to 4.",
    f"All code and data were created during this sprint and are available at [{REPO}]({REPO}): the generators, synthetic states, protocols, raw model and query records, analyses and audit receipts, with offline commands that reconstruct Studies 2 to 4.")
rep("MiniLab shows that distributed multi-agent attacks can evade trajectory monitors [2].",
    "This study grew out of John Kitaoka's talk at the sprint and the MiniLab paper [2], which shows that distributed multi-agent attacks can evade trajectory monitors.")
p.write_text(s, encoding="utf-8")
print("repo link and Kitaoka credit added")
