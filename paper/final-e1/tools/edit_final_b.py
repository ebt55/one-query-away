"""Final-b: save ~3 lines in Methods (no content removed) so Results starts on page 4."""
import pathlib

p = pathlib.Path(__file__).resolve().parent.parent / "manuscript-final.md"
s = p.read_text(encoding="utf-8")


def rep(a, b):
    global s
    assert a in s, a[:90]
    s = s.replace(a, b, 1)


rep("and that it should abstain if neither conclusion is established.\n\n"
    "Study 3 applies the same triplet design to data-consent workflows, again with 30 bases and six templates.",
    "and that it should abstain if neither conclusion is established. "
    "Study 3 applies the same triplet design to data-consent workflows, again with 30 bases and six templates.")
rep("The forecast targets one component of risk, because a judge can also accuse after seeing distinguishing evidence. Appendix C sets out what this design can and cannot identify.",
    "The forecast targets one component of risk, since a judge can also accuse after seeing distinguishing evidence (Appendix C).")
p.write_text(s, encoding="utf-8")
print("final-b applied")
