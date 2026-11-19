"""营业日边界：跟随营业场次（开门/闭店事实）而非自然日零点。"""
import pytest
from app.errors import DomainError
from app.business_day.calendar import (build, resolve_settings, segment,
                                       assign, affected_business_days,
                                       summarize)


def _calc(buckets, low=10, high=20):
    return [{'id': b, 'lower': low, 'upper': high} for b in buckets]


# 夜场 20:00 开门跨零点到次日 02:00（本地 +08），次晚 20:00 再开新场
NIGHT = ['2026-11-16T12:00:00+00:00', '2026-11-16T13:00:00+00:00',
         '2026-11-16T16:00:00+00:00', '2026-11-16T17:00:00+00:00',
         '2026-11-17T12:00:00+00:00', '2026-11-17T13:00:00+00:00']


def test_cross_midnight_session_keeps_opening_day():
    cal = build(_calc(NIGHT), resolve_settings({}))
    days = [s['business_day'] for s in cal['sessions']]
    # 跨零点的 00:00、01:00 桶仍归入开门日 2026-11-16，不被自然日零点切开
    assert days == ['2026-11-16', '2026-11-17']
    first = cal['sessions'][0]
    assert first['bucket_count'] == 4
    assert first['start'] == '2026-11-16T12:00:00+00:00'
    assert first['end'] == '2026-11-16T18:00:00+00:00'


def test_every_bucket_assigned_to_exactly_one_business_day():
    cal = build(_calc(NIGHT), resolve_settings({}))
    mapping = cal['bucket_business_day']
    assert set(mapping) == set(NIGHT) and len(mapping) == len(set(NIGHT))
    # 汇总不重不漏：桶数之和等于输入桶数
    assert sum(r['buckets'] for r in cal['reports']) == len(NIGHT)


def test_gap_inferred_split_not_fixed_midnight():
    # 无任何固定切点；仅因相邻桶空档超过 gap_hours 才分场
    sessions = segment(NIGHT, resolve_settings({'gap_hours': 6}))
    assert len(sessions) == 2 and all(s['source'] == 'inferred' for s in sessions)
    # 收窄到 3 小时仍能在跨日 19 小时间断处切分，且不会误切同场内 2-3 小时桶距
    assert len(segment(NIGHT, resolve_settings({'gap_hours': 3}))) == 2


def test_explicit_boundary_splits_short_gap_session():
    # 两场仅隔 3 小时（提前开门），默认空档推断会误并为一场
    short = ['2026-11-16T12:00:00+00:00', '2026-11-16T15:00:00+00:00',
             '2026-11-16T19:00:00+00:00', '2026-11-16T20:00:00+00:00']
    assert len(segment(short, resolve_settings({}))) == 1
    params = {'boundaries': [
        {'type': 'close', 'at': '2026-11-16T16:00:00+00:00'},
        {'type': 'open', 'at': '2026-11-16T19:00:00+00:00'}]}
    sessions = segment(short, resolve_settings(params))
    assert len(sessions) == 2
    assert all(s['source'] == 'boundary' for s in sessions)
    assert [s['business_day'] for s in sessions] == ['2026-11-16', '2026-11-17']


def test_timezone_only_changes_label_not_utc_buckets():
    near = ['2026-11-16T16:00:00+00:00', '2026-11-16T17:00:00+00:00']
    cal_sh = build(_calc(near), resolve_settings({'timezone': 'Asia/Shanghai'}))
    cal_ny = build(_calc(near),
                   resolve_settings({'timezone': 'America/New_York'}))
    # UTC 桶集合保持不变，时区只影响标签
    assert set(cal_sh['bucket_business_day']) == set(cal_ny['bucket_business_day'])
    assert cal_sh['sessions'][0]['business_day'] == '2026-11-17'
    assert cal_ny['sessions'][0]['business_day'] == '2026-11-16'


def test_affected_days_scoped_to_changed_labels():
    old = build(_calc(NIGHT), resolve_settings({'timezone': 'Asia/Shanghai'}))
    new = build(_calc(NIGHT),
                resolve_settings({'timezone': 'America/New_York'}))
    days = affected_business_days(old['bucket_business_day'],
                                  new['bucket_business_day'])
    assert days <= {'2026-11-16', '2026-11-17'}
    # 无变化时受影响集合为空
    same = affected_business_days(old['bucket_business_day'],
                                  old['bucket_business_day'])
    assert same == set()


def test_summarize_is_idempotent_per_bucket():
    mapping = {'b1': 'd1', 'b2': 'd1', 'b3': 'd2'}
    calcs = [{'id': 'b1', 'lower': 5, 'upper': 8},
             {'id': 'b2', 'lower': 7, 'upper': 9},
             {'id': 'b3', 'lower': 3, 'upper': 4}]
    reports = summarize(calcs, mapping)
    d1 = next(r for r in reports if r['business_day'] == 'd1')
    assert (d1['lower'], d1['upper'], d1['buckets']) == (12, 17, 2)
    # 重复喂入同一桶不会二次累计
    again = summarize(calcs + [calcs[0]], mapping)
    d1b = next(r for r in again if r['business_day'] == 'd1')
    assert d1b['lower'] == 12 and d1b['buckets'] == 2


def test_invalid_timezone_and_boundary_rejected():
    with pytest.raises(DomainError) as e:
        resolve_settings({'timezone': 'Foo/Bar'})
    assert e.value.code == 'TIMEZONE'
    with pytest.raises(DomainError) as e:
        resolve_settings({'boundaries': [{'type': 'x', 'at': '2026-11-16T16:00Z'}]})
    assert e.value.code == 'BOUNDARY'
    with pytest.raises(DomainError):
        resolve_settings({'boundaries': [{'type': 'open', 'at': '2026-11-16 16:00'}]})


def test_assign_rejects_bucket_outside_any_session():
    sessions = segment(NIGHT, resolve_settings({}))
    with pytest.raises(DomainError) as e:
        assign(NIGHT + ['2030-01-01T00:00:00+00:00'], sessions)
    assert e.value.code == 'BOUNDARY'
