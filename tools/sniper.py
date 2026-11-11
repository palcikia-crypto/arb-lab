#!/usr/bin/env python3
"""tools/sniper.py — автопилот arb-lab: спот-снайпер Vast.ai.

Что делает:
  1. Ищет самый дешёвый rentable-оффер нужного GPU-класса (как market_watch).
  2. Если цена <= MAX_PRICE — создаёт инстанс с vLLM (image vllm/vllm-openai),
     бид чуть выше цены оффера (interruptible, вдвое дешевле on-demand).
  3. Если у нас уже есть инстанс и рынок подорожал (цена > MAX_PRICE * 1.5) — убивает его.
  Инференс stateless: если инстанс прервали, снайпер просто поднимет новый на другом оффере.

DRY RUN (без денег и ключа):
  DRY_RUN=1 python3 tools/sniper.py
  — ничего не арендует, только печатает, какое решение принял бы: какой оффер,
    по какой цене, уложился ли в MAX_PRICE. Идеально, чтобы посмотреть
    "мозг" снайпера ДО пополнения баланса.

Требует (для боевого режима): VAST_API_KEY — секрет репозитория или переменная окружения.
Тело запроса создания инстанса сверить с https://docs.vast.ai/api-reference/creating-instances-with-api
при первом живом запуске — Vast иногда меняет схему.

Запуск: python3 tools/sniper.py   (env: GPU, MODEL, MAX_PRICE, VAST_API_KEY, DRY_RUN)
"""
import json, os, time, urllib.parse, urllib.request

BASE = 'https://console.vast.ai/api/v0'


def req(method, path, body=None, key=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header('Authorization', 'Bearer ' + key)
    r.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(r, timeout=30) as f:
        return json.load(f)


def cheapest(gpu, key=None):
    """Фильтрованный поиск как в market_watch: ключ не нужен, API публичный."""
    q = urllib.parse.quote(json.dumps(
        {'gpu_name': {'eq': gpu}, 'rentable': {'eq': True}, 'num_gpus': {'eq': 1}}))
    r = urllib.request.Request(BASE + '/bundles/?q=' + q, headers={'User-Agent': 'arb-lab/1.0'})
    with urllib.request.urlopen(r, timeout=30) as f:
        d = json.load(f)
    off = [o for o in (d.get('offers') or [])
           if isinstance(o.get('dph_total'), (int, float)) and 0 < o['dph_total'] < 25]
    off.sort(key=lambda o: o['dph_total'])
    return off[0] if off else None


def my_instances(key):
    d = req('GET', '/instances/', key=key)
    return [i for i in (d.get('instances') or []) if (i.get('label') or '').startswith('arb-lab')]


def create(offer, model, key):
    body = {
        'client_id': 'arb-lab-%d' % int(time.time()),
        'image': 'vllm/vllm-openai:latest',
        'label': 'arb-lab-vllm',
        'env': {'MODEL': model},
        'onstart_cmd': 'bash -c "MODEL=%s /onstart-cmd.sh || (pip install -q vllm && python3 -m vllm.entrypoints.openai.api_server --host 0.0.0.0 --port 8000 --model $MODEL)"' % model,
        'ssh': False,
        'jupyter': False,
        'direct_port_count': 1,
        'price': round(offer['dph_total'] * 1.02, 4),  # бид чуть выше -> прерываемый, дешевле
    }
    return req('PUT', '/asks/%d/' % offer['id'], body=body, key=key)


def destroy(inst, key):
    req('DELETE', '/instances/%d/' % inst['id'], key=key)


def main():
    dry = os.environ.get('DRY_RUN', '') in ('1', 'true', 'yes')
    key = os.environ.get('VAST_API_KEY')
    gpu = os.environ.get('GPU', 'RTX 4090')
    model = os.environ.get('MODEL', 'Qwen/Qwen3-8B')
    max_price = float(os.environ.get('MAX_PRICE', '0.30'))
    tag = '[sniper-dry]' if dry else '[sniper]'

    off = cheapest(gpu)
    now_price = off['dph_total'] if off else None
    print('%s рынок %s: самый дешёвый оффер #%s по $%s/ч (порог $%s, модель %s)' %
          (tag, gpu, off['id'] if off else '—', now_price, max_price, model))

    if dry:
        # ---- режим rehearsal: ничего не создаём, только показываем решение ----
        if not off:
            print('%s ВЕРДИКТ: офферов нет, ждать' % tag)
        elif now_price <= max_price:
            print('%s ВЕРДИКТ: АРЕНДОВАЛ БЫ оффер #%s по $%s/ч (~$%s за 24ч, ~$%s за 3 дня)' %
                  (tag, off['id'], now_price, round(now_price * 24, 2), round(now_price * 72, 2)))
            print('%s далее: vLLM + %s -> endpoint 8000 -> заявка OpenRouter' % (tag, model))
        else:
            print('%s ВЕРДИКТ: ЖДАЛ БЫ — мин. цена $%s выше порога $%s (пересчёт через 2 часа)' %
                  (tag, now_price, max_price))
        return

    if not key:
        print('[sniper] нет VAST_API_KEY — ключ на https://cloud.vast.ai/account/credentials')
        print('[sniper] прогон в режиме репетиции: DRY_RUN=1 python3 tools/sniper.py')
        return
    running = my_instances(key)
    if running:
        cur = running[0]
        print('[sniper] инстанс #%d уже работает, рынок: %s' % (cur['id'], now_price))
        if now_price and now_price > max_price * 1.5:
            print('[sniper] рынок подорожал — убиваю инстанс')
            destroy(cur, key)
        return
    if not off:
        print('[sniper] нет офферов %s' % gpu)
        return
    if now_price > max_price:
        print('[sniper] мин %s > MAX %s — ждём просадки' % (now_price, max_price))
        return
    d = create(off, model, key)
    print('[sniper] создан инстанс из оффера #%d по %s$/ч: %s' %
          (off['id'], off['dph_total'], json.dumps(d)[:300]))


if __name__ == '__main__':
    main()
