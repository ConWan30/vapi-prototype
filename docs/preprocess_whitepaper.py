"""
Preprocess vapi-whitepaper-v3.md — replace Unicode symbols with LaTeX inline commands.
Skips fenced code blocks and inline code spans.
"""
import re, pathlib, sys

INPUT  = pathlib.Path(r"C:\Users\Contr\vapi-pebble-prototype\docs\vapi-whitepaper-v3.md")
OUTPUT = pathlib.Path(r"C:\Users\Contr\vapi-pebble-prototype\docs\_whitepaper_clean.md")

src = INPUT.read_text(encoding="utf-8")

# Ordered: longest multi-char sequences first
SUBS = [
    ("\u26a0\ufe0f", "(!)" ),   # ⚠️ (emoji with variation selector)
    ("\u26a0",       "(!)" ),   # ⚠
    ("\ufe0f",       ""    ),   # bare variation selector
    ("\u26d4",       "(X)" ),   # ⛔
    ("\u2713",       r"$\checkmark$"),   # ✓
    ("\u2717",       r"$\times$"),      # ✗
    ("\u2265",       r"$\geq$"),        # ≥
    ("\u2264",       r"$\leq$"),        # ≤
    ("\u2248",       r"$\approx$"),     # ≈
    ("\u2260",       r"$\neq$"),        # ≠
    ("\u2208",       r"$\in$"),         # ∈
    ("\u2209",       r"$\notin$"),      # ∉
    ("\u03b1",       r"$\alpha$"),      # α
    ("\u03c3",       r"$\sigma$"),      # σ
    ("\u03b5",       r"$\varepsilon$"), # ε
    ("\u230a",       r"$\lfloor$"),     # ⌊
    ("\u230b",       r"$\rfloor$"),     # ⌋
    ("\u2074",       r"$^{4}$"),        # ⁴
    ("\u2075",       r"$^{5}$"),        # ⁵
    ("\u2076",       r"$^{6}$"),        # ⁶
    ("\u2077",       r"$^{7}$"),        # ⁷
    ("\u2078",       r"$^{8}$"),        # ⁸
]

# Split on fenced code blocks (```...``` or ~~~...~~~)
fence_re = re.compile(r'(```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~)', re.DOTALL)
segments = fence_re.split(src)

out_parts = []
for i, seg in enumerate(segments):
    if i % 2 == 1:
        out_parts.append(seg)   # fenced code block — untouched
        continue
    # Outside fenced blocks: split further on inline `code`
    inline_re = re.compile(r'(`[^`\n]+`)')
    sub_segs = inline_re.split(seg)
    processed = []
    for j, s in enumerate(sub_segs):
        if j % 2 == 1:
            processed.append(s)  # inline code — untouched
        else:
            for old, new in SUBS:
                s = s.replace(old, new)
            processed.append(s)
    out_parts.append("".join(processed))

result = "".join(out_parts)

OUTPUT.write_text(result, encoding="utf-8")
print(f"Written: {OUTPUT}")
print(f"Input size:  {len(src):,} chars")
print(f"Output size: {len(result):,} chars")
