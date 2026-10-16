"""Phase 4 strict schema adoption — PostgreSQL 16 catalogue fingerprints.

The two Phase 4 migrations (`20261015_booking_attempt_events`, `20261016_notification_sent_at`)
and the start-up preflight decide whether an object is EXACTLY a permitted shape. They compare
what this module reads from the catalogue with frozen literals each of them holds. Nothing here
writes, and nothing here knows what the expected shape is.

What is read is catalogue STRUCTURE (`pg_attribute`, `pg_constraint`, `pg_index`, `pg_trigger`,
`pg_proc`, `pg_rewrite`, `pg_policy`, `pg_sequence`, `pg_inherits`) and, where an expression
has no structural form (a CHECK, a column default), PostgreSQL's own deparse of the stored node
tree — never text a user typed, never a substring, never a raw DDL string. The output form is
frozen together with those literals: changing what is read means re-certifying them, and the
literal tests compare them with a hand-written DDL oracle, so no production constant is its own
oracle.

A deparse is not unique for one stored constraint: PostgreSQL 16 deparses a CHECK that was
restored from `pg_dump` differently from the same CHECK as created (STRICT-SCHEMA-ADOPTION
independent review, SSRV-01), and the restored form is a fixpoint of further dump/restore
rounds. A caller that must accept a restored database therefore passes the FINITE list of exact
fingerprints it permits (`matching`); nothing here normalises, rewrites or compares text
loosely.

Every value is catalogue metadata. No row value is read, so a refusal message carries none.
"""
from __future__ import annotations

from sqlalchemy import text

#: The PostgreSQL major version the frozen literals were measured on (server 16.13).
CERTIFIED_MAJOR = 16


class SchemaShapeRefused(RuntimeError):
    """An object is not exactly one of the shapes the caller permits. Nothing was changed."""


def server_major(conn) -> int:
    return int(conn.execute(text("SELECT current_setting('server_version_num')")).scalar()) // 10000


def require_certified_server(conn, who: str) -> None:
    major = server_major(conn)
    if major != CERTIFIED_MAJOR:
        raise SchemaShapeRefused(
            f"{who}: the frozen schema fingerprints are certified for PostgreSQL "
            f"{CERTIFIED_MAJOR} only; this server is PostgreSQL {major}. Refusing — "
            "re-certify the fingerprints for this major version first.")


def _oid(conn, table: str):
    return conn.execute(text(
        "SELECT c.oid FROM pg_class c WHERE c.relname = :t "
        "AND c.relnamespace = current_schema()::regnamespace"), {"t": table}).scalar()


def _rows(conn, sql: str, **p):
    return [tuple(tuple(v) if isinstance(v, list) else v for v in r)
            for r in conn.execute(text(sql), p).all()]


def table_fingerprint(conn, table: str) -> dict | None:
    """The complete structural fingerprint of `table` in the current schema, or None."""
    oid = _oid(conn, table)
    if oid is None:
        return None
    (rel,) = _rows(conn, """
        SELECT c.relkind, c.relpersistence, pg_get_userbyid(c.relowner) = current_user,
               c.relrowsecurity, c.relforcerowsecurity, c.relispartition, c.reloftype = 0,
               c.reltablespace = 0, c.reloptions, obj_description(c.oid, 'pg_class'),
               (SELECT count(*) FROM pg_inherits WHERE inhrelid = c.oid),
               (SELECT count(*) FROM pg_inherits WHERE inhparent = c.oid)
        FROM pg_class c WHERE c.oid = :o""", o=oid)
    keys = ("relkind", "persistence", "owned_by_current_user", "row_security",
            "force_row_security", "partition", "typed", "default_tablespace", "options",
            "comment", "inherits_from", "inherited_by")
    return {
        "table": tuple(zip(keys, rel)),
        # (name, type, NOT NULL, identity, generated, default collation, default, comment,
        #  options) in attnum order — names and order, not raw attnums (drops leave gaps)
        "columns": _rows(conn, """
            SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                   a.attidentity, a.attgenerated, a.attcollation = t.typcollation,
                   pg_get_expr(d.adbin, d.adrelid), col_description(a.attrelid, a.attnum),
                   a.attoptions
            FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
            LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid = :o AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum""", o=oid),
        "sequences": _rows(conn, """
            SELECT a.attname, sc.relname, d.deptype, format_type(s.seqtypid, NULL),
                   s.seqstart, s.seqincrement, s.seqmin, s.seqmax, s.seqcache, s.seqcycle
            FROM pg_depend d
            JOIN pg_class sc ON sc.oid = d.objid AND sc.relkind = 'S'
            JOIN pg_sequence s ON s.seqrelid = sc.oid
            JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid
            WHERE d.classid = 'pg_class'::regclass AND d.refobjid = :o
            ORDER BY 1, 2""", o=oid),
        # every constraint, CHECKs by PostgreSQL's deparse of the stored expression
        "constraints": _rows(conn, """
            SELECT con.conname, con.contype,
                   (SELECT array_agg(a.attname ORDER BY k.ord)
                      FROM unnest(con.conkey) WITH ORDINALITY k(n, ord)
                      JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n),
                   con.condeferrable, con.condeferred, con.convalidated, con.connoinherit,
                   con.conislocal, pg_get_constraintdef(con.oid)
            FROM pg_constraint con WHERE con.conrelid = :o ORDER BY 1""", o=oid),
        "referenced_by": _rows(conn, """
            SELECT con.conname, con.conrelid::regclass::text FROM pg_constraint con
            WHERE con.confrelid = :o AND con.contype = 'f' ORDER BY 1, 2""", o=oid),
        "indexes": _rows(conn, """
            SELECT ic.relname, am.amname, i.indisunique, i.indisprimary,
                   i.indnullsnotdistinct, i.indimmediate, i.indisvalid, i.indnkeyatts,
                   i.indnatts,
                   (SELECT array_agg(coalesce(a.attname, '<expression>') ORDER BY k.ord)
                      FROM unnest(i.indkey::int2[]) WITH ORDINALITY k(n, ord)
                      LEFT JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.n),
                   i.indoption::int2[]::text,
                   (SELECT array_agg(o.opcname ORDER BY k.ord)
                      FROM unnest(i.indclass::oid[]) WITH ORDINALITY k(c, ord)
                      JOIN pg_opclass o ON o.oid = k.c),
                   (SELECT array_agg(coalesce(co.collname, '') ORDER BY k.ord)
                      FROM unnest(i.indcollation::oid[]) WITH ORDINALITY k(c, ord)
                      LEFT JOIN pg_collation co ON co.oid = k.c),
                   pg_get_expr(i.indexprs, i.indrelid), pg_get_expr(i.indpred, i.indrelid),
                   ic.reloptions
            FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid
            JOIN pg_am am ON am.oid = ic.relam
            WHERE i.indrelid = :o ORDER BY 1""", o=oid),
        # every user trigger (FK triggers are internal and covered by the constraints)
        "triggers": _rows(conn, """
            SELECT t.tgname, t.tgtype, t.tgenabled, p.proname,
                   p.pronamespace = current_schema()::regnamespace, t.tgnargs,
                   t.tgattr::int2[]::text, pg_get_expr(t.tgqual, t.tgrelid),
                   t.tgconstraint = 0, t.tgdeferrable, t.tginitdeferred, t.tgoldtable,
                   t.tgnewtable
            FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
            WHERE t.tgrelid = :o AND NOT t.tgisinternal ORDER BY 1""", o=oid),
        "rules": _rows(conn, "SELECT rulename FROM pg_rewrite WHERE ev_class = :o ORDER BY 1",
                       o=oid),
        "policies": _rows(conn, "SELECT polname FROM pg_policy WHERE polrelid = :o ORDER BY 1",
                          o=oid),
    }


def function_fingerprint(conn, name: str) -> list:
    """Every function called `name` in the current schema (overloads included)."""
    return _rows(conn, """
        SELECT p.proname, l.lanname, format_type(p.prorettype, NULL), p.pronargs, p.prokind,
               p.prosecdef, p.provolatile, p.proisstrict, p.proretset, p.proconfig,
               pg_get_userbyid(p.proowner) = current_user, md5(p.prosrc)
        FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang
        WHERE p.proname = :n AND p.pronamespace = current_schema()::regnamespace
        ORDER BY p.oid""", n=name)


def column_fingerprint(conn, table: str, column: str) -> dict | None:
    """One column's fingerprint plus what can falsify it: the table's user triggers, rewrite
    rules, row security and inheritance/partitioning, and every constraint and index that
    references the column (by key, INCLUDE list, expression or predicate — the catalogue's own
    column references and recorded dependencies, never text). Constraints and indexes that do
    not reference the column are not read. `column` is None in it when the column is absent
    (and then nothing can reference it); the whole result is None when the table is."""
    oid = _oid(conn, table)
    if oid is None:
        return None
    col = _rows(conn, """
        SELECT a.attnum, format_type(a.atttypid, a.atttypmod), a.attnotnull, a.attidentity,
               a.attgenerated, a.attcollation = t.typcollation,
               pg_get_expr(d.adbin, d.adrelid), col_description(a.attrelid, a.attnum),
               a.attoptions
        FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = :o AND a.attname = :c AND a.attnum > 0 AND NOT a.attisdropped""",
        o=oid, c=column)
    keys = ("type", "not_null", "identity", "generated", "default_collation", "default",
            "comment", "options")
    attnum = col[0][0] if col else None
    (rel,) = _rows(conn, """
        SELECT c.relrowsecurity, c.relkind, c.relispartition,
               (SELECT count(*) FROM pg_inherits WHERE inhrelid = c.oid),
               (SELECT count(*) FROM pg_inherits WHERE inhparent = c.oid)
        FROM pg_class c WHERE c.oid = :o""", o=oid)
    depends = ("EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = '{cls}'::regclass "
               "AND d.objid = {obj} AND d.refclassid = 'pg_class'::regclass "
               "AND d.refobjid = :o AND d.refobjsubid = :n)")
    return {
        "column": tuple(zip(keys, col[0][1:])) if col else None,
        "table_triggers": _rows(conn, "SELECT tgname FROM pg_trigger WHERE tgrelid = :o "
                                      "AND NOT tgisinternal ORDER BY 1", o=oid),
        "table_rules": _rows(conn, "SELECT rulename FROM pg_rewrite WHERE ev_class = :o "
                                   "ORDER BY 1", o=oid),
        "table_row_security": rel[0],
        "table_inheritance": tuple(zip(("relkind", "partition", "inherits_from",
                                        "inherited_by"), rel[1:])),
        # (table, name, type) of every constraint on either side that references the column
        "column_constraints": [] if attnum is None else _rows(conn, f"""
            SELECT con.conrelid::regclass::text, con.conname, con.contype
            FROM pg_constraint con
            WHERE (con.conrelid = :o AND :n = ANY (con.conkey))
               OR (con.confrelid = :o AND :n = ANY (con.confkey))
               OR {depends.format(cls="pg_constraint", obj="con.oid")}
            ORDER BY 1, 2""", o=oid, n=attnum),
        # every index on the table whose keys, INCLUDE list, expressions or predicate use it
        "column_indexes": [] if attnum is None else _rows(conn, f"""
            SELECT ic.relname FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid
            WHERE i.indrelid = :o
              AND (:n = ANY (i.indkey::int2[])
                   OR {depends.format(cls="pg_class", obj="i.indexrelid")})
            ORDER BY 1""", o=oid, n=attnum),
    }


def matching(permitted: list[dict], found: dict | None) -> dict | None:
    """The permitted fingerprint `found` equals EXACTLY, or None. `permitted` is a caller's
    finite, frozen list; equality is whole-fingerprint equality, nothing looser."""
    if found is None:
        return None
    return next((p for p in permitted if p == found), None)


def closest_differences(permitted: list[dict], found: dict | None) -> list[str]:
    """`differences` against the permitted fingerprint `found` is nearest to (for a refusal
    message: what to look at first)."""
    return min((differences(p, found) for p in permitted), key=len)


def differences(expected: dict, found: dict | None) -> list[str]:
    """Every differing part, expected vs found (catalogue metadata only)."""
    if found is None:
        return ["object absent"]
    out = []
    for part in sorted(set(expected) | set(found)):
        e, f = expected.get(part, "<not expected>"), found.get(part, "<not read>")
        if e == f:
            continue
        if isinstance(e, list) and isinstance(f, list):
            missing = [x for x in e if x not in f]
            unexpected = [x for x in f if x not in e]
            if missing or unexpected:
                out.append(f"{part}: missing {missing!r}; unexpected {unexpected!r}")
            else:
                out.append(f"{part}: order differs; expected {e!r}; found {f!r}")
        else:
            out.append(f"{part}: expected {e!r}; found {f!r}")
    return out
