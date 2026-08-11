#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""17단계 신규 수치 수집 — 인플레·고용·유동성·섹터 특화 원천 (docs/data/ext/)."""
import csv, json, os, urllib.request
from datetime import datetime

os.makedirs('docs/data/ext', exist_ok=True)
FRED = os.environ.get('FRED_API_KEY', '')
UA = {'User-Agent': 'Mozilla/5.0 (research collector)'}

def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore')

def save(name, rows, cols=('Date', 'Close')):
    with open(f'docs/data/ext/{name}.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(cols); w.writerows(rows)
    print(f'{name}: {len(rows)}행 {rows[0][0] if rows else "-"}~{rows[-1][0] if rows else "-"}')

FRED_SERIES = [
    ('DFII5', 'real5y'), ('T5YIE', 'bei5y'), ('T10YIE', 'bei10y'),
    ('DTWEXBGS', 'usd_broad'), ('CCSA', 'cont_claims'), ('GASREGW', 'gasoline'),
    ('WALCL', 'fed_bs'), ('RRPONTSYD', 'rrp'), ('SOFR', 'sofr'),
    ('STLFSI4', 'stlfsi'), ('ANFCI', 'anfci'), ('MICH', 'mich_infl'),
    ('UNRATE', 'unrate'), ('PAYEMS', 'payems'), ('JTSJOL', 'jolts'),
    ('CES0500000003', 'ahe'), ('CPIAUCSL', 'cpi_idx'), ('UMCSENT', 'umcsent'),
    ('DGS1', 'y1'), ('DGS2', 'y2_hist'), ('DFF', 'effr_full'),
    ('BAMLH0A0HYM2', 'hy_oas_full'), ('BAMLC0A0CM', 'ig_oas_full'),
    ('BAMLH0A3HYC', 'ccc_oas'), ('WLCFLPCL', 'disc_window'), ('TOTCI', 'ci_loans'), ('DRTSCILM', 'sloos_ci'),
    ('DCPF3M', 'cp3m'), ('DTB3', 'tb3m'), ('DPSACBW027SBOG', 'deposits'), ('NFCICREDIT', 'nfci_credit'), ('NFCILEVERAGE', 'nfci_lev'),
    ('NFCIRISK', 'nfci_risk'), ('COMPOUT', 'cp_out'), ('TOTBKCR', 'bank_credit'),
    ('DBAA', 'baa_full'), ('DAAA', 'aaa_full'),
]
for sid, name in FRED_SERIES:
    try:
        j = json.loads(get(f'https://api.stlouisfed.org/fred/series/observations?series_id={sid}&api_key={FRED}&file_type=json&observation_start=1996-12-01'))
        rows = [[o['date'], float(o['value'])] for o in j['observations'] if o['value'] != '.']
        save(name, rows)
    except Exception as e:
        print(f'{name} 실패: {e}')

YAHOO_SYMS = [
    ('TLT', 'tlt'), ('HYG', 'hyg'), ('LQD', 'lqd'), ('TIP', 'tip'),
    ('XLE', 'xle'), ('XLU', 'xlu'), ('XLI', 'xli'), ('XLV', 'xlv'),
    ('KRE', 'kre'), ('XBI', 'xbi'), ('ARKK', 'arkk'), ('SLV', 'slv'),
    ('USO', 'uso'), ('DBA', 'dba'), ('UUP', 'uup'), ('EEM', 'eem'),
    ('FXI', 'fxi'), ('GDX', 'gdx'), ('VNQ', 'vnq'), ('RSP', 'rsp'),
    ('NG=F', 'natgas'), ('^GSPC', 'spx'),
    ('JPY=X', 'usdjpy'), ('CNY=X', 'usdcny'), ('CHF=X', 'usdchf'), ('FXY', 'fxy'), ('CEW', 'cew'),
    ('ARCC', 'arcc'), ('BIZD', 'bizd'), ('FSK', 'fsk'), ('MAIN', 'main_bdc'), ('PSEC', 'psec'),
    ('APO', 'apo'), ('ARES', 'ares'), ('OWL', 'owl'), ('BX', 'bx'), ('KKR', 'kkr'),
    ('JAAA', 'jaaa'), ('JBBB', 'jbbb'), ('BKLN', 'bkln'), ('SRLN', 'srln'),
    ('VWEHX', 'vwehx'), ('FAGIX', 'fagix'), ('PFF', 'pff'), ('REM', 'rem'),
    ('ANGL', 'angl'), ('EMB', 'emb'), ('CWB', 'cwb'), ('KIE', 'kie'),
    ('IAI', 'iai'), ('KBWB', 'kbwb'), ('IEF', 'ief'), ('SHY', 'shy'),
]
for sym, name in YAHOO_SYMS:
    try:
        j = json.loads(get(f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}?period1=1041379200&period2=9999999999&interval=1d'))
        res = j['chart']['result'][0]
        rows = [[datetime.utcfromtimestamp(t).strftime('%Y-%m-%d'), round(c, 4)]
                for t, c in zip(res['timestamp'], res['indicators']['quote'][0]['close']) if c]
        save(name, rows)
    except Exception as e:
        print(f'{name} 실패: {e}')


# OFR FSI 신용 서브지수 (전 역사)
try:
    txt = get('https://www.financialresearch.gov/financial-stress-index/data/fsi.csv')
    import io as _io
    rows = []
    rd = csv.DictReader(_io.StringIO(txt))
    cred_col = None
    for fn in (rd.fieldnames or []):
        if fn and fn.strip().lower() == 'credit': cred_col = fn
    for r in rd:
        d0 = r.get('Date') or r.get('date')
        try: rows.append([d0, float(r[cred_col])])
        except Exception: pass
    save('ofr_credit', rows)
except Exception as e:
    print(f'ofr_credit 실패: {e}')


# CFTC 엔 선물 비상업 포지션 (레거시, 전 역사)
try:
    j = json.loads(get("https://publicreporting.cftc.gov/resource/6dca-aqww.json?cftc_contract_market_code=097741&$limit=4000&$select=report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all&$order=report_date_as_yyyy_mm_dd"))
    rows = []
    for r in j:
        try:
            net = float(r['noncomm_positions_long_all']) - float(r['noncomm_positions_short_all'])
            rows.append([r['report_date_as_yyyy_mm_dd'][:10], net])
        except Exception: pass
    save('jpy_cot', rows)
except Exception as e:
    print(f'jpy_cot 실패: {e}')

print('17단계 수집 완료')
