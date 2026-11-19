"""营业日报 HTTP 编排：首次发布、换时区/补录边界定向重算、页面展示区间。"""
from pathlib import Path

NIGHT_CSV = (Path(__file__).resolve().parents[1] /
             'sample_data' / 'night.csv').read_text(encoding='utf-8')


def _submit(client):
    return client.post('/api/runs', json={'csv': NIGHT_CSV}).get_json()


def test_reconcile_exposes_business_day_intervals(client):
    s = _submit(client)
    days = [x['business_day'] for x in s['business_day']['sessions']]
    assert days == ['2026-11-16', '2026-11-17']
    # 跨零点桶归入开门日
    mapping = s['business_day']['bucket_business_day']
    assert mapping['2026-11-16T17:00:00+00:00'] == '2026-11-16'


def test_daily_report_page_renders_official_intervals(client):
    _submit(client)
    page = client.get('/m6_reconcile')
    assert page.status_code == 200
    html = page.data.decode()
    assert '正式营业日区间' in html and '2026-11-16' in html


def test_recompute_first_publish_then_idempotent(client):
    s = _submit(client)
    body = {'run_id': s['run_id'], 'parameters': {}}
    first = client.post('/api/daily_reports/recompute', json=body).get_json()
    assert sorted(first['upserted']) == ['2026-11-16', '2026-11-17']
    # 默认 recompute_id 对相同请求确定性 -> 重跑全部跳过
    again = client.post('/api/daily_reports/recompute', json=body).get_json()
    assert again['upserted'] == [] and len(again['skipped']) == 2
    published = client.get('/api/daily_reports').get_json()
    assert {d['business_day'] for d in published} == \
        {'2026-11-16', '2026-11-17'}


def test_timezone_switch_scopes_recalc(client):
    s = _submit(client)
    client.post('/api/daily_reports/recompute',
                json={'run_id': s['run_id'], 'parameters': {}})
    r = client.post('/api/daily_reports/recompute',
                    json={'run_id': s['run_id'],
                          'parameters': {'timezone': 'America/New_York'}})
    assert r.status_code == 200
    # 同参数重跑幂等
    r2 = client.post('/api/daily_reports/recompute',
                     json={'run_id': s['run_id'],
                           'parameters': {'timezone': 'America/New_York'}}).get_json()
    assert r2['upserted'] == [] and r2['skipped']


def test_boundary_backfill_marks_source(client):
    s = _submit(client)
    r = client.post('/api/daily_reports/recompute',
                    json={'run_id': s['run_id'], 'parameters': {
                        'boundaries': [
                            {'type': 'close',
                             'at': '2026-11-16T17:00:00+00:00'},
                            {'type': 'open',
                             'at': '2026-11-17T12:00:00+00:00'}]}})
    assert r.status_code == 200
    assert all(x['source'] == 'boundary'
               for x in r.get_json()['calendar']['sessions'])


def test_invalid_settings_rejected(client):
    s = _submit(client)
    bad_tz = client.post('/api/daily_reports/recompute',
                         json={'run_id': s['run_id'],
                               'parameters': {'timezone': 'X/Y'}})
    assert bad_tz.status_code == 422 and bad_tz.get_json()['code'] == 'TIMEZONE'
    bad_b = client.post('/api/daily_reports/recompute',
                        json={'run_id': s['run_id'],
                              'parameters': {'boundaries': []}})
    assert bad_b.status_code == 200  # 空边界合法


def test_get_single_daily_report_and_404(client):
    s = _submit(client)
    client.post('/api/daily_reports/recompute',
                json={'run_id': s['run_id'], 'parameters': {}})
    ok = client.get('/api/daily_reports/2026-11-16')
    assert ok.status_code == 200 and ok.get_json()['business_day'] == '2026-11-16'
    assert client.get('/api/daily_reports/1999-01-01').status_code == 404
