"""Final-e: win back ~3 lines on page 2 after adding the Kitaoka credit (no facts removed)."""
import pathlib

p = pathlib.Path(__file__).resolve().parent.parent / "manuscript-final.md"
s = p.read_text(encoding="utf-8")


def rep(a, b):
    global s
    assert a in s, a[:90]
    s = s.replace(a, b, 1)


rep("This study grew out of John Kitaoka's talk at the sprint and the MiniLab paper [2], which shows that distributed multi-agent attacks can evade trajectory monitors. "
    "The Arbiter monitors multi-agent conversations with a budget for active inspection and reports false accusations against aligned agents [3]. "
    "We study a narrower, controlled question: with identical visible inputs, does a monitor fetch the authoritative record that would defeat its own accusation?",
    "This study grew out of John Kitaoka's sprint talk and the MiniLab paper [2], which shows that distributed attacks can evade trajectory monitors. "
    "The Arbiter monitors multi-agent conversations with an inspection budget and reports false accusations against aligned agents [3]. "
    "We ask a narrower question: with identical visible inputs, does a monitor fetch the record that would defeat its own accusation?")
rep("4. **Boundaries:** in a data-consent family the pre-registered contrast was null, and a pre-registered forecast of monitor risk missed its success criterion. We report both, with a policy-ambiguity lesson for benchmark design.",
    "4. **Boundaries:** in a data-consent family the pre-registered contrast was null, and a pre-registered forecast of monitor risk missed its success criterion; we report both.")
rep("Yet monitors are usually evaluated on harmful versus ordinary benign examples, where clearing a benign agent needs no extra evidence. We ask what happens when the monitor has to go and find the record that clears an agent.",
    "Yet monitors are usually evaluated on harmful versus ordinary benign examples, where clearing a benign agent needs no extra evidence; we ask what happens when the monitor must go and find the record that clears an agent.")
p.write_text(s, encoding="utf-8")
print("final-e applied")
