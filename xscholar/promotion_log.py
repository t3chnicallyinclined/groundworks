# -*- coding: utf-8 -*-
"""promotion_log.py -- a hash-chained, append-only record of every gated promotion, and its verifier.

Switched on by `promotion_log: true` in the instance config (human-owned when present). The statements are
emitted with the gates (gates.gate_surql), so a deployment that applies the gates records promotions.

    log_surql(inst)                    the log and head tables, and one event per gate rule
    verify(db, anchor=None) -> dict    recompute the chain; the first broken link, by sequence number
    export_anchor(db, inst, path)      write the head outside the database, after verifying the chain
    late_promotions(db)                entries whose promoter was provisioned after a writer first wrote the row

    python -m xscholar.promotion_log INSTANCE.yaml --verify [--anchor FILE] --url URL --ns NS --db DB
    python -m xscholar.promotion_log INSTANCE.yaml --export-anchor FILE --url URL --ns NS --db DB
    python -m xscholar.promotion_log INSTANCE.yaml --late --url URL --ns NS --db DB

What an entry holds: the rule, table, row, field, the value before and after (as the datastore renders them),
the credential that made the change (or `root`), that credential's declared principal and provisioning time,
the datastore clock, a heuristic flag (`late`), the previous entry's hash, and its own hash. The hash covers
every one of those fields, each length-prefixed so no two different entries share a hash input.

How the chain is kept linear (measured on SurrealDB 3.1.6, memory, rocksdb and surrealkv): the entry id IS
its sequence number, and the head is one record every promotion rewrites. Two promotions that commit at the
same time both read the same head and both try to write the same entry id; the datastore lets one commit
and refuses the other with "Transaction conflict ... This transaction can be retried", and the refused
promotion does not happen at all. Measured with 60 concurrent promotions: 18 to 26 refused, never a fork,
never a gap, log entries equal to promoted rows. The cost: promotions are serialised, and a caller that
promotes concurrently sees retryable refusals.

A refused promotion (an independence THROW, a failed ASSERT) aborts the whole write, including its entry.

What this cannot do, stated plainly: root owns the datastore and can rewrite the whole chain from the first
entry, recomputing every hash, and the result verifies. The defence is an ANCHOR outside the database: export
the head (`export_anchor`) to a file the instance commits or stores somewhere root on the database host
cannot reach, and verify against it. A rewrite or truncation behind an anchored entry then fails. Entries
after the most recent anchor are only as trustworthy as the database. The log's own fields are READONLY and
an event refuses UPDATE and DELETE, which stops an accident, not the owner: removing them is a schema change
that `xscholar.live_schema` reports, if it runs while they are removed.
Standard library only.
"""
import argparse
import datetime as _dt
import hashlib
import json
import pathlib
import sys

import pathlib as _pathlib, sys as _sys
if __package__ in (None, ""):  # run as a plain script: make the repo root importable
    _sys.path.insert(0, str(next(d for d in _pathlib.Path(__file__).resolve().parents if (d / "pyproject.toml").exists())))
from xscholar import config  # noqa: E402
from xscholar import db as dbmod  # noqa: E402

LOG_TABLE = "promotion_log"
HEAD_TABLE = "promotion_head"
HEAD_ID = "head"
GENESIS = "0" * 64
# The hashed fields, in hash order. `seq` and `prev` are hashed too, so an entry cannot be moved or re-linked.
FIELDS = ("seq", "prev", "rule", "on_table", "row", "field", "before", "after", "credential", "principal",
          "at", "provisioned_at", "late")
APPEND_ONLY_MARKER = "the promotion log is append-only"


class LogError(Exception):
    pass


def _lit(text):
    return "'%s'" % str(text).replace("\\", "\\\\").replace("'", "\\'")


def _value_literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _lit(value)


def material(entry):
    """The exact text an entry's hash is taken over: every field, length-prefixed, joined by '|'."""
    parts = []
    for name in FIELDS:
        text = str(entry.get(name)) if name != "seq" else str(int(entry.get("seq")))
        parts.append("%d:%s" % (len(text), text))
    return "|".join(parts)


def entry_hash(entry):
    return hashlib.sha256(material(entry).encode("utf-8")).hexdigest()


# ---- surql -----------------------------------------------------------------------------------------

def _material_surql():
    parts = []
    for name in FIELDS:
        value = "<string> $e.%s" % name
        parts.append("<string> string::len(%s), ':', %s" % (value, value))
    return "string::concat(%s)" % ", '|', ".join(parts)


def log_surql(inst):
    """The log, its head, and one event per gate rule. Empty when `promotion_log` is off."""
    if not inst.promotion_log_enabled():
        return []
    lines = ["-- promotion log: hash-chained record of every gated promotion (xscholar/promotion_log.py)",
             "DEFINE TABLE OVERWRITE %s SCHEMAFULL PERMISSIONS NONE;" % LOG_TABLE,
             "DEFINE FIELD OVERWRITE seq ON %s TYPE int READONLY;" % LOG_TABLE]
    for name in FIELDS[1:] + ("hash",):
        lines.append("DEFINE FIELD OVERWRITE %s ON %s TYPE string READONLY;" % (name, LOG_TABLE))
    lines += [
        "DEFINE EVENT OVERWRITE %s_append_only ON TABLE %s WHEN $event != 'CREATE' THEN { THROW %s + ': ' + "
        "$event + ' of ' + <string> ($before.id ?? $after.id) + ' refused'; };"
        % (LOG_TABLE, LOG_TABLE, _lit(APPEND_ONLY_MARKER)),
        "DEFINE TABLE OVERWRITE %s SCHEMAFULL PERMISSIONS NONE;" % HEAD_TABLE,
        "DEFINE FIELD OVERWRITE seq ON %s TYPE int;" % HEAD_TABLE,
        "DEFINE FIELD OVERWRITE hash ON %s TYPE string;" % HEAD_TABLE,
    ]
    written_by = config.INDEPENDENCE_FIELD
    for rule in inst.gate_rules():
        field, value = rule["field"], _value_literal(rule["value"])
        lines.append(
            "DEFINE EVENT OVERWRITE %s ON TABLE %s WHEN $after.%s = %s AND ($before.%s ?? NONE) != %s THEN { "
            "LET $head = type::record('%s', '%s'); "
            "LET $seq = ($head.seq ?? 0) + 1; "
            "LET $firsts = array::map($before.%s ?? [], |$c| type::record('row_writer', [$after.id, $c]).first_at); "
            "LET $late = IF $auth = NONE { 'unmeasured: made by root, which has no credential' } "
            "ELSE IF $auth.provisioned_at = NONE { 'unmeasured: the promoting credential has no provisioning time' } "
            "ELSE IF array::len($firsts) = 0 OR NONE INSIDE $firsts { 'unmeasured: a writer of the row has no recorded first write' } "
            "ELSE IF $auth.provisioned_at > array::min($firsts) { 'yes' } ELSE { 'no' }; "
            "LET $e = { seq: $seq, prev: $head.hash ?? '%s', rule: %s, on_table: %s, row: <string> $after.id, "
            "field: %s, before: <string> ($before.%s ?? NONE), after: <string> $after.%s, "
            "credential: IF $auth = NONE { 'root' } ELSE { <string> $auth }, principal: <string> ($auth.principal ?? 'none'), "
            "at: <string> time::now(), provisioned_at: <string> ($auth.provisioned_at ?? 'none'), late: $late }; "
            "LET $hash = crypto::sha256(%s); "
            "CREATE type::record('%s', $seq) CONTENT object::extend($e, { hash: $hash }); "
            "UPSERT type::record('%s', '%s') SET seq = $seq, hash = $hash; "
            "};  -- %s"
            % (event_name(rule), rule["table"], field, value, field, value, HEAD_TABLE, HEAD_ID, written_by,
               GENESIS, _lit(rule["id"]), _lit(rule["table"]), _lit(field), field, field, _material_surql(),
               LOG_TABLE, HEAD_TABLE, HEAD_ID, rule["id"]))
    return lines


def event_name(rule):
    import re
    return "%s_promotion_log" % re.sub(r"[^A-Za-z0-9_]", "_", str(rule["id"]))


# ---- verification ----------------------------------------------------------------------------------

def read_chain(db):
    if getattr(db, "access", None):
        raise LogError("the promotion log is readable only by root; this connection is an agent credential")
    entries = db.sql("SELECT * FROM %s ORDER BY seq;" % LOG_TABLE, expect=1)[0] or []
    head = db.sql("SELECT * FROM ONLY type::record('%s', '%s');" % (HEAD_TABLE, HEAD_ID), expect=1)[0]
    return entries, head


def verify(db, anchor=None):
    """Recompute the chain. Returns {ok, entries, first_break, head, anchor}.

    `first_break` is {seq, why} for the first entry that does not hold, or for the head or the anchor. An
    empty log verifies only when no anchor says it once held entries."""
    entries, head = read_chain(db)
    result = {"ok": True, "entries": len(entries), "first_break": None, "head": None, "anchor": None,
              "late": sum(1 for e in entries if e.get("late") == "yes")}
    if head:
        result["head"] = {"seq": head.get("seq"), "hash": head.get("hash")}

    def broken(seq, why):
        if result["first_break"] is None:
            result["first_break"] = {"seq": seq, "why": why}
        result["ok"] = False

    prev = GENESIS
    for index, entry in enumerate(entries):
        seq = entry.get("seq")
        if seq != index + 1:
            broken(index + 1, "entry %d is missing: the next entry held is seq %r (removed, or never written)"
                   % (index + 1, seq))
            break
        if str(entry.get("id")) != "%s:%d" % (LOG_TABLE, seq):
            broken(seq, "entry seq %d is stored under id %s" % (seq, entry.get("id")))
            break
        if entry.get("prev") != prev:
            broken(seq, "entry %d does not link to entry %d: prev is %s, entry %d's hash is %s"
                   % (seq, seq - 1, entry.get("prev"), seq - 1, prev))
            break
        if entry_hash(entry) != entry.get("hash"):
            broken(seq, "entry %d's content does not match its hash: a field was changed after it was written"
                   % seq)
            break
        prev = entry["hash"]
    if result["ok"]:
        if entries and not head:
            broken(len(entries), "the log holds %d entries and there is no head record" % len(entries))
        elif head and (head.get("seq") != len(entries) or head.get("hash") != (entries[-1]["hash"] if entries else None)):
            broken(head.get("seq"), "the head says seq %r hash %s, the last entry is seq %d hash %s: entries after "
                   "it were removed, or the head was rewritten" % (head.get("seq"), head.get("hash"), len(entries),
                                                                  entries[-1]["hash"] if entries else None))
    if anchor is not None:
        result["anchor"] = {"seq": anchor.get("seq"), "hash": anchor.get("hash")}
        seq = anchor.get("seq")
        if not isinstance(seq, int) or seq < 0:
            raise LogError("the anchor holds no usable sequence number: %r" % (anchor,))
        if seq > len(entries):
            broken(seq, "the anchor was taken at seq %d and the log now holds %d entries: it was truncated"
                   % (seq, len(entries)))
        elif seq and entries[seq - 1].get("hash") != anchor.get("hash"):
            broken(seq, "entry %d's hash is %s and the anchor recorded %s: the chain up to the anchor was rewritten"
                   % (seq, entries[seq - 1].get("hash"), anchor.get("hash")))
    return result


def checkpoint_text(anchor):
    """The anchor as the body of a C2SP tlog-checkpoint note (https://c2sp.org/tlog-checkpoint): origin line,
    size, base64 hash, blank line. So a witness or a timestamping service that speaks that format can hold it.

    Two things this is NOT, said where it is written: it is unsigned (a signature line needs a key this module
    does not hold), and the hash is the head of a hash CHAIN, not an RFC 6962 Merkle tree root, so a witness
    cannot check a consistency proof between two checkpoints -- it can only keep and countersign them."""
    import base64
    origin = "xscholar/promotion_log/%s/%s/%s sha256-chain" % (anchor["instance"], anchor["ns"], anchor["db"])
    return "%s\n%d\n%s\n\n" % (origin, anchor["seq"], base64.b64encode(bytes.fromhex(anchor["hash"])).decode("ascii"))


def parse_checkpoint(text):
    import base64
    lines = text.split("\n")
    try:
        origin, size, digest = lines[0], int(lines[1]), base64.b64decode(lines[2], validate=True).hex()
    except (IndexError, ValueError) as exc:
        raise LogError("not a checkpoint body (origin, size, base64 hash): %s" % exc) from exc
    parts = origin.split(" ")[0].split("/")
    if len(parts) != 5 or parts[:2] != ["xscholar", "promotion_log"]:
        raise LogError("checkpoint origin %r is not a promotion log's" % origin)
    return {"instance": parts[2], "ns": parts[3], "db": parts[4], "seq": size, "hash": digest}


def load_anchor(path):
    """An anchor file: JSON as export_anchor writes it, or a checkpoint body (a path ending `.checkpoint`)."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise LogError("cannot read anchor %s: %s" % (path, exc)) from exc
    if str(path).endswith(".checkpoint"):
        return parse_checkpoint(text)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise LogError("cannot read anchor %s: %s" % (path, exc)) from exc


def export_anchor(db, inst, path):
    """Write the verified head to `path` (a checkpoint body when it ends `.checkpoint`, JSON otherwise). Refuses a
    broken chain, and refuses to replace an anchor the chain no longer contains: overwriting it would destroy
    the only evidence that the chain was rewritten."""
    path = pathlib.Path(path)
    previous = load_anchor(path) if path.exists() else None
    result = verify(db, anchor=previous)
    if not result["ok"]:
        raise LogError("not exporting an anchor: %s" % result["first_break"]["why"])
    anchor = {"instance": inst.id, "ns": db.ns, "db": db.db, "seq": result["entries"],
              "hash": (result["head"] or {}).get("hash") if result["entries"] else GENESIS,
              "exported_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if str(path).endswith(".checkpoint"):
        path.write_text(checkpoint_text(anchor), encoding="utf-8")
    else:
        path.write_text(json.dumps(anchor, indent=2) + "\n", encoding="utf-8")
    return anchor


def late_promotions(db):
    """Entries whose promoting credential was provisioned AFTER some writer of the row first wrote it.

    A heuristic, not a finding: it is the signature of creating a second login to get past an independence
    rule, and it is also what an honest reviewer who joined later looks like."""
    entries, _ = read_chain(db)
    return [e for e in entries if e.get("late") == "yes"]


# ---- command line --------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify or anchor the promotion log.")
    parser.add_argument("instance")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--anchor", help="compare the chain against this anchor file")
    parser.add_argument("--export-anchor", metavar="FILE")
    parser.add_argument("--late", action="store_true", help="list promotions by a credential provisioned late")
    parser.add_argument("--url", default="http://127.0.0.1:8002/sql")
    parser.add_argument("--ns")
    parser.add_argument("--db")
    args = parser.parse_args(argv)
    inst = config.load(args.instance)
    if not inst.promotion_log_enabled():
        print("NOT EXECUTED: promotion_log is not switched on in %s" % args.instance)
        return 2
    conn = dbmod.Db(url=args.url, ns=args.ns or inst.id, db=args.db or inst.id)
    try:
        conn.ping()
        if args.export_anchor:
            anchor = export_anchor(conn, inst, args.export_anchor)
            print("ANCHOR written to %s: seq %d hash %s; commit it or store it off this host"
                  % (args.export_anchor, anchor["seq"], anchor["hash"]))
            return 0
        if args.late:
            rows = late_promotions(conn)
            for e in rows:
                print("LATE (heuristic) seq %s: %s %s.%s -> %s by %s, provisioned %s, after a writer of the row "
                      "first wrote it" % (e["seq"], e["rule"], e["row"], e["field"], e["after"], e["credential"],
                                          e["provisioned_at"]))
            print("%d promotion(s) by a credential provisioned after the row was first written" % len(rows))
            return 0
        if args.verify or args.anchor:
            result = verify(conn, anchor=load_anchor(args.anchor) if args.anchor else None)
            print("PROMOTION LOG: %d entries, %s%s" % (
                result["entries"], "chain intact" if result["ok"] else "BROKEN at seq %s: %s"
                % (result["first_break"]["seq"], result["first_break"]["why"]),
                "" if args.anchor else " (no anchor given: a chain rewritten from the start would also verify)"))
            return 0 if result["ok"] else 1
    except (LogError, dbmod.DbError) as exc:
        print("NOT EXECUTED: %s" % exc)
        return 2
    parser.error("choose --verify, --export-anchor or --late")


if __name__ == "__main__":
    sys.exit(main())
