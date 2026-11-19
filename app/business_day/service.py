"""营业日报定向重算编排。

换时区或补录边界时：先按新设置重建场次与营业日归属，对比旧归属找出受影响
营业日，只重算并发布这些营业日；同一 recompute_id 重跑不重复累计。
首次发布（该批营业日尚无正式日报）时，全部营业日视为受影响并落库。
"""
from app.business_day.calendar import (build, resolve_settings,
                                       affected_business_days)
from app import daily_report


def recompute(calculations, old_calendar, parameters, recompute_id):
    settings = resolve_settings(parameters)
    new_cal = build(calculations, settings)
    all_days = {r['business_day'] for r in new_cal['reports']}
    published_days = {d['business_day'] for d in
                      daily_report.list_published()}
    if old_calendar and all_days <= published_days:
        # 定向重算：仅归属发生变化的营业日需要重算。
        days = affected_business_days(old_calendar.get('bucket_business_day', {}),
                                      new_cal['bucket_business_day'])
    else:
        # 首次发布：尚未落库的营业日全部视为受影响范围。
        days = all_days - published_days
    if not days:
        # 无营业日归属变化：仍交存储层做同批幂等判定，便于如实回报 skipped。
        days = all_days
    # 全部报告交给存储层，由其按受影响范围过滤并保证同批幂等。
    result = daily_report.recalc_and_publish(new_cal['reports'],
                                             recompute_id, days)
    result['affected_days'] = sorted(days)
    result['calendar'] = new_cal
    return result
