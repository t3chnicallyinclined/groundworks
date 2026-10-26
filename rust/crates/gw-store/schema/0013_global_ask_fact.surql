-- scope: global
-- A candidate that is a recorded fact keeps the fact's text in the packet (2026-10-26), so a quote of the fact
-- checks against what the reader saw: the fact and its evidence unit together. Measured in eval v2 r4: readers
-- quoted the fact's words and cited its evidence unit, and the check refused 4 of 11 answers for it.
DEFINE FIELD OVERWRITE candidates.*.fact ON ask_packet TYPE option<string>;
