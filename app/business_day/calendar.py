"""营业日（business day）边界引擎。

裁决：营业日边界跟随“营业场次”这一营业事实，而非自然日零点或固定凌晨切点。
一场从开门到闭店的连续营业构成一个营业日，营业日标签取该场次在营业时区下
的开门日。场次之间以观测时间线上的闭店/开门空档（超过 gap_hours）切分；
临时闭店或提前开门可由 parameters.boundaries 显式补录，补录边界优先于推断。

时间线始终在 UTC 小时桶上计算，时区只影响营业日标签与展示区间，不改动桶本身。
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from app.errors import DomainError

# 默认闭店/开门空档：相邻小时桶间隔超过该值视为两场之间。
DEFAULT_GAP_HOURS = 6
DEFAULT_TIMEZONE = 'Asia/Shanghai'
# 单场不允许跨越多于该天数，防止把长期断档误并为同一场。
MAX_SPAN_HOURS = 36


def _parse_dt(value):
    if isinstance(value, datetime):
        d = value
    else:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise DomainError('BOUNDARY', '边界时间必须包含时区偏移')
    return d.astimezone(timezone.utc)


def _parse_boundary(raw):
    """补录边界：{'at': 带偏移ISO时间, 'type': 'open'|'close'}。"""
    if not isinstance(raw, dict):
        raise DomainError('BOUNDARY', '边界必须是对象')
    kind = str(raw.get('type', '')).strip().lower()
    if kind not in ('open', 'close'):
        raise DomainError('BOUNDARY', "边界类型必须是 open 或 close")
    return {'at': _parse_dt(raw['at']), 'type': kind}


def resolve_settings(parameters):
    params = parameters or {}
    tz_name = params.get('timezone') or DEFAULT_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        raise DomainError('TIMEZONE', '时区标识无效: %s' % tz_name)
    gap = params.get('gap_hours', DEFAULT_GAP_HOURS)
    try:
        gap = float(gap)
    except (TypeError, ValueError):
        raise DomainError('PARAMETERS', 'gap_hours 必须是数值')
    if gap <= 0 or gap > MAX_SPAN_HOURS:
        raise DomainError('PARAMETERS', 'gap_hours 超出允许范围')
    raw_boundaries = params.get('boundaries') or []
    if not isinstance(raw_boundaries, list):
        raise DomainError('BOUNDARY', 'boundaries 必须是数组')
    boundaries = sorted((_parse_boundary(b) for b in raw_boundaries),
                        key=lambda b: b['at'])
    return {'timezone': tz, 'timezone_name': tz_name, 'gap_hours': gap,
            'boundaries': boundaries}


def _inferred_splits(times, gap):
    """依据相邻小时桶空档推断的切点（切点位于相邻两场之间）。"""
    splits = []
    for prev, nxt in zip(times, times[1:]):
        if (nxt - prev) > timedelta(hours=gap):
            splits.append(prev + (nxt - prev) / 2)
    return splits


def _explicit_splits(boundaries, start, end):
    """补录的 open/close 事实对：相邻的 close->open 之间形成切点。

    只接受落在观测时间线跨度内的边界；落不进来的边界不产生切点，避免误切。
    """
    splits = []
    for prev, nxt in zip(boundaries, boundaries[1:]):
        if prev['type'] == 'close' and nxt['type'] == 'open':
            cut = prev['at'] + (nxt['at'] - prev['at']) / 2
            if start < cut < end:
                splits.append(cut)
    return splits


def segment(buckets, settings):
    """把 UTC 小时桶序列切成营业场次。

    buckets: 已去重、升序的小时桶 ISO 字符串（UTC，带偏移）。
    返回 [{start, end, business_day, timezone, gap_hours, bucket_count,
           source: 'inferred'|'boundary'}]，区间端点为 UTC ISO。
    """
    if not buckets:
        return []
    times = sorted({_parse_dt(b) for b in buckets})
    start, end = times[0], times[-1]
    inferred = _inferred_splits(times, settings['gap_hours'])
    explicit = _explicit_splits(settings['boundaries'], start, end)
    explicit_marks = {d.isoformat() for d in explicit}
    cuts = sorted({d.isoformat(): d for d in inferred + explicit}.values())

    tz = settings['timezone']
    sessions = []
    cursor = 0
    for cut in cuts:
        members = [t for t in times[cursor:] if t < cut]
        if not members:
            continue
        source = 'boundary' if cut.isoformat() in explicit_marks else 'inferred'
        sessions.append(_make_session(members, tz, source, settings))
        cursor += len(members)
    tail = times[cursor:]
    if tail:
        source = 'inferred'
        for b in settings['boundaries']:
            if b['type'] == 'open' and abs(
                    (b['at'] - tail[0]).total_seconds()) \
                    <= settings['gap_hours'] * 3600:
                source = 'boundary'
        sessions.append(_make_session(tail, tz, source, settings))
    return sessions


def _make_session(members, tz, source, settings):
    first, last = members[0], members[-1]
    span = (last - first).total_seconds() / 3600
    if span > MAX_SPAN_HOURS:
        raise DomainError('BOUNDARY', '单场营业跨度超出上限，疑似边界缺失')
    return {
        'start': first.isoformat(),
        'end': (last + timedelta(hours=1)).isoformat(),
        'business_day': first.astimezone(tz).date().isoformat(),
        'timezone': settings['timezone_name'],
        'gap_hours': settings['gap_hours'],
        'bucket_count': len(members),
        'source': source,
    }


def assign(buckets, sessions):
    """返回 {bucket_iso: business_day}，每个桶恰好归入一场，不重不漏。"""
    mapping = {}
    ranges = [(datetime.fromisoformat(s['start']),
               datetime.fromisoformat(s['end']), s['business_day'])
              for s in sessions]
    for bucket in buckets:
        t = _parse_dt(bucket)
        hits = [day for lo, hi, day in ranges if lo <= t < hi]
        if len(hits) != 1:
            raise DomainError('BOUNDARY', '小时桶未唯一归入营业日: %s' % bucket)
        mapping[bucket] = hits[0]
    return mapping


def build(calculations, settings):
    """主入口：依据 M5 的逐桶区间结果构造营业日日历与日报汇总。"""
    buckets = sorted({c['id'] for c in calculations})
    sessions = segment(buckets, settings)
    mapping = assign(buckets, sessions)
    reports = summarize(calculations, mapping)
    return {'settings': {'timezone': settings['timezone_name'],
                         'gap_hours': settings['gap_hours'],
                         'boundary_count': len(settings['boundaries'])},
            'sessions': sessions, 'reports': reports,
            'bucket_business_day': mapping}


def summarize(calculations, mapping):
    """按营业日汇总逐桶区间；同一桶在一次汇总中只计一次（不重复累计）。"""
    by_day = {}
    seen = set()
    for c in calculations:
        day = mapping[c['id']]
        key = (day, c['id'])
        if key in seen:
            continue
        seen.add(key)
        item = by_day.setdefault(day, {'business_day': day, 'lower': 0,
                                       'upper': 0, 'buckets': 0})
        item['lower'] += c['lower']
        item['upper'] += c['upper']
        item['buckets'] += 1
    return [by_day[d] for d in sorted(by_day)]


def affected_business_days(old_mapping, new_mapping):
    """换时区或补录边界后，仅返回归属发生变化的营业日标签集合。

    包含旧版与新版中受影响的全部标签，便于只重算对应营业日。
    """
    days = set()
    for bucket, new_day in new_mapping.items():
        old_day = old_mapping.get(bucket)
        if old_day != new_day:
            days.add(new_day)
            if old_day is not None:
                days.add(old_day)
    for bucket, old_day in old_mapping.items():
        if bucket not in new_mapping:
            days.add(old_day)
    return days
