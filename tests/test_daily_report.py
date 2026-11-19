"""营业日报持久化：定向重算、同批幂等、发布失败保留旧版。"""
import pytest
from app import daily_report
from app.errors import DomainError


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'daily.db'))
    return daily_report


def _report(day, low=100, high=200):
    return {'business_day': day, 'lower': low, 'upper': high, 'buckets': 4}


def test_first_publish_creates_reports(db):
    r = db.recalc_and_publish([_report('2026-11-16')], 'run-1')
    assert r['upserted'] == ['2026-11-16'] and r['skipped'] == []
    got = db.get_published('2026-11-16')
    assert got['lower'] == 100 and got['version'] == 1


def test_same_batch_rerun_does_not_double_count(db):
    db.recalc_and_publish([_report('2026-11-16')], 'run-1')
    again = db.recalc_and_publish([_report('2026-11-16', low=999)], 'run-1')
    assert again['upserted'] == [] and again['skipped'] == ['2026-11-16']
    # 同批重跑既不新增版本也不覆盖数值
    got = db.get_published('2026-11-16')
    assert got['lower'] == 100 and got['version'] == 1


def test_new_batch_switches_version_and_supersedes_old(db):
    db.recalc_and_publish([_report('2026-11-16')], 'run-1')
    db.recalc_and_publish([_report('2026-11-16', low=120)], 'run-2')
    got = db.get_published('2026-11-16')
    assert got['lower'] == 120 and got['version'] == 2
    # 列表只返回 published，不返回 superseded
    assert len([d for d in db.list_published()
                if d['business_day'] == '2026-11-16']) == 1


def test_affected_scope_limits_writes(db):
    db.recalc_and_publish([_report('2026-11-16'), _report('2026-11-17')], 'run-1')
    # 仅允许重算 17 日；16 日即便出现在报告里也不得被写
    r = db.recalc_and_publish(
        [_report('2026-11-16', low=1), _report('2026-11-17', low=220)],
        'run-2', affected_days={'2026-11-17'})
    assert r['upserted'] == ['2026-11-17']
    assert db.get_published('2026-11-16')['lower'] == 100
    assert db.get_published('2026-11-17')['lower'] == 220


def test_failed_publish_keeps_old_report(db):
    db.recalc_and_publish([_report('2026-11-16')], 'run-1')
    with pytest.raises(TypeError):
        db.publish_or_keep('2026-11-16', {'lower': object()}, 'run-fail')
    got = db.get_published('2026-11-16')
    assert got['lower'] == 100 and got['version'] == 1


def test_missing_day_404(db):
    with pytest.raises(DomainError) as e:
        db.get_published('1999-01-01')
    assert e.value.status == 404


def test_recompute_id_required(db):
    with pytest.raises(DomainError):
        db.recalc_and_publish([_report('2026-11-16')], '')
