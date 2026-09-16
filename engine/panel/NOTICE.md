# Notice: adapted ideas

This module adapts ideas from **Council of High Intelligence**
(https://github.com/0xNyk/council-of-high-intelligence), MIT License, Copyright (c) 2026 nyk.
Read in full at commit `94f9a90d62cb5963a7ee64340206465e1c86c146` on 2026-09-15.

No code was copied. Reused from the source: the stance line format (`STANCE | CONFIDENCE | DEALBREAKER`), the confidence weight table and tally arithmetic (1.5x seat, high 1.0 / med 0.75 / low 0.5, base-weight total, 2/3 bar), and two short instruction phrases in the anti-conformity and counterfactual prompts. Other prompt wording is our own.

## Changes from the source

| Idea | In the source | Here |
|---|---|---|
| Blind first round, anonymized cross-examination | The coordinator model masks names by instruction | A program writes the anonymized bundle, fixes label order from the lock hash, and hashes round-1 answers so later edits fail |
| Anti-conformity directive | Prompt instruction to name the flaw before updating | A changed stance is refused unless a `FLAW:` line quotes the seat's own round-1 words verbatim |
| Weighted seat chosen before positions | Coordinator designates it at step 0 | The whole panel file is hashed and locked before any answer exists; every later command refuses on an edit |
| Structured stance line, re-prompt rather than infer | Coordinator re-prompts | Program parses, names the seat, and refuses to tally |
| Dissent quota, novelty gate, agreement check | Coordinator `[VERIFY]` steps | Program checks distinct objection categories, `NEW:` lines absent from round 1, and counterfactual files from the two least confident majority seats; tally refuses until they pass |
| Confidence-weighted tally, split to the user, minority report | Coordinator arithmetic | Same arithmetic in code; a split exits 3 |

Not adapted: personas, round 3, the chairman synthesis, provider routing, installers.

The source's changelog cites research for anonymization, anti-conformity and confidence weighting (arXiv:2510.07517, 2509.11035, 2509.16839, 2509.14034, 2410.12853). Those papers have **not** been read here; they are pointers.

## License of the source

MIT License

Copyright (c) 2026 nyk

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
