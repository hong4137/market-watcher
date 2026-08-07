#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""일회성 확장 계기 전 역사 수집 (탐사층용) — docs/data/ext/ 에 저장.
FRED(신용·실질금리·3M) + Cboe(VXN·VVIX·SKEW·VIX3M·VIX9D) + 야후(금·BTC·QQQE·QQQ) + OFR FSI."""
import csv, json, os, io, urllib.request
from datetime import datetime

os.makedirs('docs/data/ext', exist_ok=True)
FRED = os.environ.get('FRED_API_KEY', '')
UA = {'User-Agent': 'Mozilla/5.0 (research collector)'}

def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore')

def save(name, rows, cols):
    with open(f'docs/data/ext/{name}.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(cols); w.writerows(rows)
    print(f'{name}: {len(rows)}행 {rows[0][0] if rows else "-"}~{rows[-1][0] if rows else "-"}')

# FRED 계열 (2019-06부터 — chg 계산 여유)
FULL = {'ndx_full': 'NASDAQ100', 'y10_hist': 'DGS10', 'y3m_hist': 'DGS3MO'}
for name, sid in FULL.items():
    try:
        j = json.loads(get(f'https://api.stlouisfed.org/fred/series/observations?series_id={sid}&api_key={FRED}&file_type=json&observation_start=1985-01-01'))
        rows = [[o['date'], float(o['value'])] for o in j['observations'] if o['value'] != '.']
        save(name, rows, ['Date', 'Close'])
    except Exception as e:
        print(f'{name} 실패: {e}')

for sid, name in [('BAMLH0A0HYM2', 'hy_oas'), ('BAMLC0A0CM', 'ig_oas'),
                  ('DFII10', 'real10y'), ('DGS3MO', 'y3m'), ('DGS10', 'y10_full'),
                  ('DGS30', 'y30'), ('DGS5', 'y5'), ('T5YIFR', 't5yifr'),
                  ('ICSA', 'claims'), ('NFCI', 'nfci')]:
    try:
        j = json.loads(get(f'https://api.stlouisfed.org/fred/series/observations?series_id={sid}&api_key={FRED}&file_type=json&observation_start=2019-06-01'))
        rows = [[o['date'], float(o['value'])] for o in j['observations'] if o['value'] != '.']
        save(name, rows, ['Date', 'Close'])
    except Exception as e:
        print(f'{name} 실패: {e}')

# Cboe 계열 (전 역사)
for sym, name in [('VXN', 'vxn'), ('VVIX', 'vvix'), ('SKEW', 'skew'),
                  ('VIX3M', 'vix3m'), ('VIX9D', 'vix9d')]:
    try:
        txt = get(f'https://cdn.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv')
        rows = []
        for r in csv.DictReader(io.StringIO(txt)):
            d0 = r.get('DATE') or list(r.values())[0]
            try:
                m, dd, y = d0.split('/')
                v = r.get('CLOSE') or r.get(sym) or list(r.values())[-1]
                rows.append([f'{y}-{int(m):02d}-{int(dd):02d}', float(v)])
            except Exception: pass
        save(name, rows, ['Date', 'Close'])
    except Exception as e:
        print(f'{name} 실패: {e}')

# 야후 계열 (10년)
for sym, name in [('GC=F', 'gold'), ('BTC-USD', 'btc'), ('QQQE', 'qqqe'), ('QQQ', 'qqq'), ('^MOVE', 'move_full'),
                  ('HG=F', 'copper'), ('IWM', 'iwm'), ('IYT', 'iyt'), ('XLY', 'xly'), ('XLP', 'xlp'), ('XLF', 'xlf')]:
    try:
        j = json.loads(get(f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}?range=max&interval=1d'))
        res = j['chart']['result'][0]
        rows = [[datetime.utcfromtimestamp(t).strftime('%Y-%m-%d'), round(c, 3)]
                for t, c in zip(res['timestamp'], res['indicators']['quote'][0]['close']) if c]
        save(name, rows, ['Date', 'Close'])
    except Exception as e:
        print(f'{name} 실패: {e}')

# OFR 금융스트레스지수
try:
    txt = get('https://www.financialresearch.gov/financial-stress-index/data/fsi.csv')
    rows = []
    for r in csv.DictReader(io.StringIO(txt)):
        d0 = r.get('Date') or r.get('date')
        v = r.get('OFR FSI') or r.get('ofr_fsi') or r.get('OFR_FSI')
        try: rows.append([d0, float(v)])
        except Exception: pass
    save('ofr_fsi', rows, ['Date', 'Close'])
except Exception as e:
    print(f'ofr_fsi 실패: {e}')

print('수집 완료')
