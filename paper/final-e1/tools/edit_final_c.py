"""Final-c: in 4.1, place the Controls lead-in + Table 1 right after Figure 2, then the lookup paragraph and the template note."""
import pathlib

p = pathlib.Path(__file__).resolve().parent.parent / "manuscript-final.md"
s = p.read_text(encoding="utf-8")

look_s = s.index("**What the monitors looked up.**")
look_e = s.index("\n\n", look_s) + 2
ctrl_s = s.index("**Controls.**")
tab_cap_s = s.index("*Table 1. Study 2 controls.")
ctrl_e = s.index("\n\n", tab_cap_s) + 2
assert look_s < ctrl_s, "unexpected order"
look = s[look_s:look_e]
ctrl = s[ctrl_s:ctrl_e]
s = s[:look_s] + ctrl + look + s[ctrl_e:]
p.write_text(s, encoding="utf-8")
print("4.1 reordered: Figure 2 -> Controls + Table 1 -> lookup paragraph -> template note")
