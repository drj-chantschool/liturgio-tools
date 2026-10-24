"""The chants_import re-sync rule, against a fake connection (no DB)."""
from liturgio_tools import chants_import as ci


def gb(id_, **kw):
    row = {c: None for c in ci.UPSTREAM_COLS}
    row.update({'id': id_, 'incipit': f'Chant {id_}', 'gabc': '(c4) A(f)', **kw})
    return row


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)

    def scalars(self):
        return iter(self.rows)


class FakeConn:
    def __init__(self, staging, have, referenced=()):
        self.staging, self.have, self.referenced = staging, have, list(referenced)

    def execute(self, sql, params=None):
        s = str(sql)
        if 'FROM gregobase_chants' in s:
            return _Result(self.staging)
        if 'origin_pending_hash' in s:
            return _Result(self.have)
        return _Result(self.referenced)


def have(row, curated=0, pending=None, hash_=None):
    return {'origin_id': str(row['id']), 'origin_hash': hash_ or ci.origin_hash(row),
            'origin_pending_hash': pending, 'is_curated': curated}


def test_new_rows_take_the_given_group_or_get_a_singleton():
    a, b = gb(1), gb(2)
    plan = ci.build_plan(FakeConn([a, b], []), group_for={1: 10})
    assert [(r['id'], g) for r, g in plan.inserts] == [(1, 10), (2, None)]
    assert [r['id'] for r in plan.new_groups] == [2]


def test_duplicates_and_stubs_skipped_unless_referenced():
    rows = [gb(1, duplicateof=5), gb(2, duplicateof=-1), gb(3, incipit=''), gb(4, incipit='')]
    plan = ci.build_plan(FakeConn(rows, [], referenced=['gregobase:1', 'gregobase:4']))
    assert [r['id'] for r, _ in plan.inserts] == [1, 4]
    assert plan.kept_dups == [1] and plan.skipped_dups == 1 and plan.skipped_stubs == 1


def test_changed_uncurated_is_reimported_curated_is_flagged_once():
    old = gb(1)
    new = dict(old, gabc='(c4) B(g)')
    plan = ci.build_plan(FakeConn([new], [have(old)]))
    assert [r['id'] for r in plan.updates] == [1] and not plan.flagged

    plan = ci.build_plan(FakeConn([new], [have(old, curated=1)]))
    assert not plan.updates and [(r['id'], h) for r, h in plan.flagged] == [(1, ci.origin_hash(new))]

    # already flagged with this upstream version: nothing more to do
    plan = ci.build_plan(FakeConn([new], [have(old, curated=1, pending=ci.origin_hash(new))]))
    assert not plan.updates and not plan.flagged and plan.unchanged == 1


def test_unchanged_row_is_left_alone():
    row = gb(1)
    plan = ci.build_plan(FakeConn([row], [have(row, curated=1)]))
    assert plan.unchanged == 1 and not (plan.inserts or plan.updates or plan.flagged)


def test_chant_values_build_elements_and_hash():
    v = ci.chant_values(gb(7, gabc_verses='2. B(g) (::)', **{'office-part': 'hy'}))
    assert v['chant_uuid'] == 'gregobase:7' and v['office_part'] == 'hy' and v['origin_id'] == '7'
    assert '"continues": true' in v['elements'] and len(v['origin_hash']) == 64
