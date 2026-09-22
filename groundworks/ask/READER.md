# Reading an ask packet

You have been handed a directory made by `groundworks ask open`. It holds one question and the units the
ranker pulled for it, cut into slices. Your job is to read all of it and answer from it, in a form a
program can audit. The program is `groundworks ask check`; it does not trust you, and that is the point.

## What is in the packet

- `question.json`: the question, the corpus size, how many units were retrieved (`retrieved`), and the
  slices with their unit ids.
- `slices/ask_NN.jsonl`: the units, one JSON object per line, full text, in rank order. Each has `id`,
  `text`, `locator`, `status`, `grade`.
- `candidates.jsonl`: the same units in one file.

## What you write

1. **`read.jsonl`**, one line per unit as you read it: `{"unit": "<id>", "slice": "<slice id>"}`.
   Append as you go. Every unit in every slice, once. A slice is not read until every unit in it is.
2. **`answer.json`**:
   ```json
   {"answer": "<plain-language answer, a few sentences>",
    "quotes": [{"unit": "<id>", "quote": "<copied character for character from that unit's text>",
                "use": "support", "why": "<what this shows, one line>"}],
    "gaps": ["<what the read units do not settle>"],
    "not_answerable": false}
   ```
   - `use: support` means the answer rests on it. The unit's `status` must be one of the instance's
     usable statuses; the checker refuses a support quote from any other.
   - `use: note` means "this exists and says this, but the answer does not rest on it". Use it for
     unusable units and for context.
   - A quote is at least three words (the instance may ask for more) and is found word for word in the
     unit, whitespace aside. Do not paraphrase inside quotation marks. Do not quote across two units.
   - Cite only units you have read. The checker refuses a cite to a unit not in `read.jsonl`.

## Then run

    groundworks ask check  <dir> --instance <instance.yaml>
    groundworks ask render <dir> --instance <instance.yaml>

`check` exits 0 on PASS, 1 on FAIL with each error naming the unit, 3 on NOT EXECUTED (nothing read,
answer missing). `render` writes `answer.md` only on PASS; its first line says how much of the corpus was
retrieved, read and **not read**. Repeat that line when you report.

## Never

- Answer from memory of the domain. If the units do not say it, the answer says the units do not say it.
- Skip a unit because it looks irrelevant. Read it, record it, move on.
- Fix a failed quote by loosening it. Go back to the unit and copy again.
- Write to a graph. An ask answer is proposal material at most; promotion is a separate, gated act.
