#!/usr/bin/env python3
"""tools/market_watch.py — мониторинг инференс-арбитража.

Каждые 2 часа (workflow watch.yml):
  1. Цены аренды GPU на Vast.ai (публичный API, без ключа): low/median по классам
  2. Цены токенов открытых моделей на OpenRouter (публичный API)
  3. Расчёт маржи $/час для связок (GPU-класс x модель) на консервативной пропускной
  4. -> data/market.json (панель index.html)
"""
import base64, json, os, time, urllib.parse, urllib.request

REPO = 'palcikia-crypto/arb-lab'
# консервативная пропускная способность vLLM, ток/с (батчинг)
TPS = {'big': {(7, 9): 1600, (12, 15): 900}, '4090': {(7, 9): 800, (12, 15): 450}}
SIZE_KEYS = ['7b', '8b', '9b', '12b', '14b']
KEYWORDS = ['qwen', 'llama', 'deepseek', 'mistral', 'gemma', 'phi', 'granite', 'olmo']

# подтвержденные точные имена gpu_name в API Vast (eq-фильтр)
VAST_CLASSES = [
    ('A100 SXM4 80GB', ['A100 SXM4']),
    ('H100 SXM 80GB', ['H100 SXM']),
    ('H100 NVL', ['H100 NVL']),
    ('H200', ['H200']),
    ('RTX 4090', ['RTX 4090']),
    ('RTX 5090', ['RTX 5090']),
    ('RTX 5080', ['RTX 5080']),
    ('RTX 3090', ['RTX 3090']),
    ('RTX A6000', ['RTX A6000']),
    ('L40S', ['L40S']),
    ('A40', ['A40']),
]


def log(m):
    print('[watch] %s' % m, flush=True)


def api(url, timeout=30, retries=2):
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'arb-lab/1.0'})
            with urllib.request.urlopen(req, timeout=timeout) as f:
                return json.load(f)
        except Exception as e:
            last = e
            time.sleep(3)
    raise last


def push_github(name, obj):
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        log('нет токена — только локально')
        return
    url = 'https://api.github.com/repos/%s/contents/data/%s' % (REPO, name)
    b64 = base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()
    sha = None
    try:
        req = urllib.request.Request(url)
        req.add_header('Authorization', 'Bearer ' + token)
        with urllib.request.urlopen(req, timeout=15) as f:
            sha = json.load(f).get('sha')
    except Exception:
        pass
    payload = {'message': 'Market watch: ' + name, 'content': b64, 'branch': 'main'}
    if sha:
        payload['sha'] = sha
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method='PUT')
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Content-Type', 'application/json')
    try:
        urllib.request.urlopen(req, timeout=20).read()
        log('pushed data/%s' % name)
    except Exception as e:
        log('push %s: %s' % (name, e))


def median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 3)


def vast_prices():
    """Лёгкие фильтрованные запросы по классам (маленький payload вместо полного каталога)."""
    res = {}
    for label, names in VAST_CLASSES:
        ps = []
        for gpu in names:
            q = urllib.parse.quote(json.dumps(
                {'gpu_name': {'eq': gpu}, 'rentable': {'eq': True}, 'num_gpus': {'eq': 1}}))
            try:
                d = api('https://console.vast.ai/api/v0/bundles/?q=' + q, timeout=25)
            except Exception as e:
                log('vast %s: %s' % (gpu, e))
                continue
            for o in d.get('offers') or []:
                p = o.get('dph_total')
                if isinstance(p, (int, float)) and p > 0:
                    ps.append(round(float(p), 3))
        if len(ps) >= 2:
            res[label] = {'low': min(ps), 'median': median(ps), 'offers': len(ps)}
    return res


def openrouter_models():
    out = []
    try:
        d = api('https://openrouter.ai/api/v1/models')
        for m in d.get('data') or []:
            mid = (m.get('id') or '').lower()
            if not m.get('hugging_face_id'):
                continue
            if not any(k in mid for k in KEYWORDS):
                continue
            size = None
            for s in SIZE_KEYS:
                if s in mid:
                    size = int(s.replace('b', ''))
                    break
            if size is None:
                continue
            pr = (m.get('pricing') or {})
            try:
                p_in = float(pr.get('prompt') or 0)
                p_out = float(pr.get('completion') or 0)
            except Exception:
                continue
            if p_in <= 0 or p_out <= 0 or p_in > 0.6:
                continue
            out.append({'id': m.get('id'), 'name': m.get('name'), 'size_b': size,
                        'price_in': p_in, 'price_out': p_out,
                        'blended_1m': round((p_in + 3 * p_out) / 4, 4)})
    except Exception as e:
        log('openrouter: %s' % e)
    seen = {}
    for x in out:
        seen[x['id']] = x
    return sorted(seen.values(), key=lambda x: x['blended_1m'])


def tps_for(size, tier):
    for (lo, hi), v in TPS[tier].items():
        if lo <= size <= hi:
            return v
    return None


def combos(models, vast):
    rows = []
    # tier 'big': датацентровые A100/H100/H200 (возьмём самый дешёвый класс)
    big_pool = {k: v for k, v in vast.items()
                if k.startswith(('A100', 'H100', 'H200'))}
    big = min([v['median'] for v in big_pool.values()] or [0]) or None
    big_low = min([v['low'] for v in big_pool.values()] or [0]) or None
    g4090 = vast.get('RTX 4090', {}).get('median')
    g4090_low = vast.get('RTX 4090', {}).get('low')
    for m in models:
        rev = lambda tps: round(tps * 3600 / 1e6 * m['blended_1m'] * 0.945, 2)
        if big:
            t = tps_for(m['size_b'], 'big')
            if t:
                rows.append({'model': m['id'], 'size': m['size_b'], 'gpu': 'big (A100/H100/H200)',
                             'tps': t, 'rev_hr': rev(t), 'rent_hr': big,
                             'margin_hr': round(rev(t) - big, 2), 'rent_low': big_low,
                             'margin_low_hr': round(rev(t) - (big_low or big), 2) if big_low else None})
        if g4090:
            t = tps_for(m['size_b'], '4090')
            if t and m['size_b'] <= 9:
                rows.append({'model': m['id'], 'size': m['size_b'], 'gpu': 'RTX 4090',
                             'tps': t, 'rev_hr': rev(t), 'rent_hr': g4090,
                             'margin_hr': round(rev(t) - g4090, 2), 'rent_low': g4090_low,
                             'margin_low_hr': round(rev(t) - (g4090_low or g4090), 2) if g4090_low else None})
    rows.sort(key=lambda r: -(r.get('margin_hr') or -99))
    return rows[:16]


def main():
    vast = vast_prices()
    models = openrouter_models()
    best = combos(models, vast)
    out = {'updated_at': int(time.time()), 'vast': vast, 'models': models[:20],
           'best': best,
           'note': 'маржа = выручка токенов (95% от цены OR) минус медианная аренда Vast; токи/с консервативные (vLLM, батч)'}
    try:
        os.makedirs('data', exist_ok=True)
        with open('data/market.json', 'w') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    push_github('market.json', out)
    log('vast classes=%d, models=%d, combos=%d' % (len(vast), len(models), len(best)))


if __name__ == '__main__':
    main()
