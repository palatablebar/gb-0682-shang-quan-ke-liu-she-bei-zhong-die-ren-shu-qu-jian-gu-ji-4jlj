"""营业日报的持久化、定向重算与原子发布。

设计要点：
- 重算按营业日定向：只替换受影响营业日的报告，未受影响的保留。
- 幂等：同一重算批次（recompute_id）重复提交不会重复累计。
- 原子发布：先写草稿，发布切版失败时旧版正式日报保持不变。
"""
import json
from app import store as base
from app.errors import DomainError


def _conn():
    db = base.connection()
    db.execute('CREATE TABLE IF NOT EXISTS daily_reports ('
               '  business_day TEXT NOT NULL,'
               '  version INTEGER NOT NULL DEFAULT 1,'
               '  status TEXT NOT NULL DEFAULT "published",'
               '  body TEXT NOT NULL,'
               '  recompute_id TEXT,'
               '  PRIMARY KEY (business_day, version))')
    return db


def _published(db, day):
    return db.execute(
        'SELECT * FROM daily_reports WHERE business_day=? AND status="published" '
        'ORDER BY version DESC LIMIT 1', (day,)).fetchone()


def list_published():
    with _conn() as db:
        rows = db.execute(
            'SELECT business_day,version,body FROM daily_reports '
            'WHERE status="published" ORDER BY business_day').fetchall()
    out = []
    for r in rows:
        body = json.loads(r['body'])
        body['business_day'] = r['business_day']
        body['version'] = r['version']
        out.append(body)
    return out


def get_published(day):
    with _conn() as db:
        row = _published(db, day)
    if not row:
        raise DomainError('NOT_FOUND', '营业日报不存在: %s' % day, 404)
    out = json.loads(row['body'])
    out['business_day'] = row['business_day']
    out['version'] = row['version']
    return out


def _next_version(db, day):
    row = db.execute(
        'SELECT MAX(version) AS v FROM daily_reports WHERE business_day=?',
        (day,)).fetchone()
    return (row['v'] or 0) + 1


def recalc_and_publish(reports, recompute_id, affected_days=None):
    """定向重算并原子发布。

    reports: 本次重算得到的营业日报列表（仅需覆盖受影响营业日）。
    recompute_id: 同一批重跑的稳定标识；重复提交直接跳过，不重复累计。
    affected_days: 限定允许写入的营业日集合；None 表示以 reports 自带标签为准。
    返回 {upserted, skipped, days}。
    """
    if not isinstance(recompute_id, str) or not recompute_id:
        raise DomainError('PARAMETERS', 'recompute_id 不能为空')
    target = set(affected_days) if affected_days is not None else \
        {r['business_day'] for r in reports}
    upserted, skipped = [], []
    db = _conn()
    try:
        db.execute('BEGIN IMMEDIATE')
        for report in reports:
            day = report['business_day']
            if day not in target:
                continue
            dup = db.execute(
                'SELECT 1 FROM daily_reports WHERE recompute_id=? '
                'AND business_day=?', (recompute_id, day)).fetchone()
            if dup:
                # 同一批重跑：幂等跳过，绝不二次累计。
                skipped.append(day)
                continue
            version = _next_version(db, day)
            db.execute('UPDATE daily_reports SET status="superseded" '
                       'WHERE business_day=? AND status="published"', (day,))
            db.execute('INSERT INTO daily_reports'
                       '(business_day,version,status,body,recompute_id) '
                       'VALUES (?,?, "published", ?, ?)',
                       (day, version,
                        json.dumps(report, ensure_ascii=False), recompute_id))
            upserted.append(day)
        db.commit()
    except Exception:
        # 发布失败：整批回滚，旧版正式日报原样保留。
        db.rollback()
        raise
    finally:
        db.close()
    return {'upserted': sorted(upserted), 'skipped': sorted(skipped),
            'recompute_id': recompute_id}


def publish_or_keep(day, draft_body, recompute_id):
    """单营业日原子切版：任何失败都保留旧版，返回是否真正切换。"""
    db = _conn()
    try:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM daily_reports WHERE recompute_id=? '
                      'AND business_day=?', (recompute_id, day)).fetchone():
            db.rollback()
            return {'switched': False, 'reason': 'idempotent'}
        version = _next_version(db, day)
        db.execute('UPDATE daily_reports SET status="superseded" '
                   'WHERE business_day=? AND status="published"', (day,))
        db.execute('INSERT INTO daily_reports'
                   '(business_day,version,status,body,recompute_id) '
                   'VALUES (?,?, "published", ?, ?)',
                   (day, version,
                    json.dumps(draft_body, ensure_ascii=False), recompute_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {'switched': True, 'version': version}
