from app.business_day.calendar import build, resolve_settings


def balance(state):
    accepted = len(state['rows']); isolated = len(state['quarantine'])
    accounted = accepted + isolated + state['blank_count'] + \
        state['header_count'] + state['duplicate_count']
    settings = resolve_settings(state.get('parameters', {}))
    # 营业日边界跟随营业场次（开门/闭店事实），而非自然日零点。
    state['business_day'] = build(state.get('calculations', []), settings)
    return {'input': state['input_count'], 'accepted': accepted,
            'isolated': isolated,
            'blank': state['blank_count'], 'headers': state['header_count'],
            'duplicates': state['duplicate_count'],
            'accounted': accounted,
            'balanced': accounted == state['input_count'],
            'business': state.get('business', {}),
            'business_day': state['business_day']}
