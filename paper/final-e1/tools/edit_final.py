"""Final: claim-discipline fixes from the v10 review. Writes manuscript-final.md from manuscript-v5.md (v10 source)."""
import pathlib

d = pathlib.Path(__file__).resolve().parent.parent
s = (d / "manuscript-v5.md").read_text(encoding="utf-8")


def rep(a, b):
    global s
    assert a in s, a[:90]
    s = s.replace(a, b, 1)


# Abstract
rep("The failure lies in what monitors look up, not in how they reason: across five monitors from three providers, none of the 120 wrongful accusations came after retrieving the clearing record. "
    "A simple rule-based router that always fetches that record got every case right, while a generic \"verify before accusing\" instruction did not help.",
    "The failure was in what monitors looked up: across five monitors from three providers, run in their standard configuration, all 120 wrongful accusations of exculpatory agents came without the clearing record ever being retrieved. "
    "A simple rule-based router that always fetches that record succeeded on all 30 triplets, while a generic \"verify before accusing\" instruction did not significantly improve results.")

# Contribution 3
rep("3. **The failure is acquisition, not interpretation:** all 120 wrongful accusations by the five monitors followed a skipped lookup; given the record, Sonnet cleared every case, and a label-blind policy router fixed all 30 triplets. A generic verification reminder did not.",
    "3. **A repeated failure to look up the clearing record:** in their standard configuration, all 120 wrongful accusations by the five monitors followed a skipped lookup; given the record, Sonnet cleared every case, and a label-blind policy router succeeded on all 30 triplets. A generic verification reminder did not significantly improve triplet success.")

# Discussion
rep("Across five monitors from three providers, every wrongful accusation came from a lookup that was never made, not from misreading evidence that was retrieved. "
    "Giving a monitor tools is not enough: it must spend its budget on the record that can settle the accusation. "
    "Prompting it to \"verify\" did little, whereas a simple policy-derived rule that always fetches the decisive record fixed every release case.",
    "Across five monitors from three providers, every wrongful accusation in their standard runs came from a lookup that was never made; retrieval was not always sufficient, as one Haiku reminder-arm error shows. "
    "Giving a monitor tools is not enough: it must spend its budget on the record that can settle the accusation. "
    "A generic \"verify\" reminder did not significantly improve triplet success (4 to 7 of 30, p = 0.375), whereas a policy-derived router that always fetches the decisive record succeeded on all 30 release triplets.")

# Conclusion
rep("Across models and providers, the problem was what monitors chose to look up, not how they reasoned about what they found.",
    "Across models and providers, these failures were dominated by skipped lookups of the clearing record.")

(d / "manuscript-final.md").write_text(s, encoding="utf-8")
print("wrote manuscript-final.md")
