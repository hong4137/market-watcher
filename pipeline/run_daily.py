#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NDX 감시 파이프라인 v0.2 — 칸별 종합 상황판
설계 철학(사용자 지시): 완전한 예측기가 아니라, 각 칸(원인 카테고리)의 관련 계기를
펼쳐놓고 사람이 종합 판단하는 상황판. 예측식이 있는 칸은 판정을, 없는 칸은 계기와
공백 사실을 그대로 명시한다. 문턱·산식은 연구 폴더 확정 자산 그대로(수정 금지).
"""
import csv, json, os, io, urllib.request
from datetime import datetime, date, timedelta

D = 'docs/data'
FRED = os.environ.get('FRED_API_KEY', '')
NTFY = os.environ.get('NTFY_TOPIC', '')
UA = {'User-Agent': 'Mozilla/5.0 (watch-bot; research pipeline)'}

def get(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore')

def load_csv(path):
    if not os.path.exists(path): return []
    with open(path, encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

def save_csv(path, rows, cols):
    allcols = list(cols)
    for r in rows:
        for k in r:
            if k and k not in allcols: allcols.append(k)
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=allcols, extrasaction='ignore', restval='')
        w.writeheader(); w.writerows(rows)

def upsert(path, datecol, valcol, pairs, overwrite=False):
    rows = load_csv(path)
    bymap = {r[datecol]: r for r in rows}
    for d_, v_ in pairs:
        if d_ in bymap:
            if overwrite: bymap[d_][valcol] = v_
        else:
            rows.append({datecol: d_, valcol: v_}); bymap[d_] = rows[-1]
    rows.sort(key=lambda r: r[datecol])
    save_csv(path, rows, [datecol, valcol])
    return rows

log = []
def note(m): log.append(m); print(m)

# ============ 1. 수집 (원천별 독립 격리) ============
# NDX — FRED 공식(겹치는 날짜 정정 덮어쓰기)
try:
    j = json.loads(get(f'https://api.stlouisfed.org/fred/series/observations?series_id=NASDAQ100&api_key={FRED}&file_type=json&observation_start=2026-05-01'))
    pairs = [(o['date'], round(float(o['value']), 2)) for o in j['observations'] if o['value'] != '.']
    ndx_rows = upsert(f'{D}/ndx_daily.csv', 'Date', 'Close', pairs, overwrite=True)
    note(f"NDX(FRED) ok ~{pairs[-1][0]}")
except Exception as e:
    ndx_rows = load_csv(f'{D}/ndx_daily.csv'); note(f"NDX 실패: {e}")

# VIX — Cboe 공식
try:
    txt = get('https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv')
    pairs = []
    for r in csv.DictReader(io.StringIO(txt)):
        d0 = r.get('DATE') or list(r.values())[0]
        try:
            m, dd, y = d0.split('/'); pairs.append((f'{y}-{int(m):02d}-{int(dd):02d}', float(r.get('CLOSE') or list(r.values())[-1])))
        except Exception: pass
    vix_rows = upsert(f'{D}/vix.csv', 'Date', 'Close', pairs[-60:])
    note(f"VIX(Cboe) ok ~{pairs[-1][0]}")
except Exception as e:
    vix_rows = load_csv(f'{D}/vix.csv'); note(f"VIX 실패: {e}")

# 야후 계열 — SMH·MOVE·DXY·WTI (실패 시 결측 허용)
def yahoo(sym, path, rng='3mo'):
    j = json.loads(get(f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}?range={rng}&interval=1d'))
    res = j['chart']['result'][0]
    pairs = [(datetime.utcfromtimestamp(t).strftime('%Y-%m-%d'), round(c, 3))
             for t, c in zip(res['timestamp'], res['indicators']['quote'][0]['close']) if c]
    return upsert(path, 'Date', 'Close', pairs)

ysrc = {'SMH': (f'{D}/smh_daily.csv', 'smh'), '^MOVE': (f'{D}/move.csv', 'move'),
        'DX-Y.NYB': (f'{D}/dxy.csv', 'dxy'), 'CL=F': (f'{D}/wti.csv', 'wti')}
ydata = {}
for sym, (path, name) in ysrc.items():
    try:
        ydata[name] = yahoo(sym, path); note(f"{name}(야후) ok ~{ydata[name][-1]['Date']}")
    except Exception as e:
        ydata[name] = load_csv(path); note(f"{name} 실패(결측 허용): {e}")

# 재무부 금리 — 공식 CSV (당해년도 전체)
try:
    yr = date.today().year
    txt = get(f'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{yr}/all?type=daily_treasury_yield_curve&field_tdr_date_value={yr}&_format=csv')
    trows = load_csv(f'{D}/treasury.csv'); bymap = {r['Date']: r for r in trows}
    for r in csv.DictReader(io.StringIO(txt)):
        try:
            m, dd, y = r['Date'].split('/'); iso = f'{y}-{int(m):02d}-{int(dd):02d}'
            y2, y10 = r.get('2 Yr'), r.get('10 Yr')
            if y2 and y10:
                if iso in bymap: bymap[iso].update({'Y2': y2, 'Y10': y10})
                else: trows.append({'Date': iso, 'Y2': y2, 'Y10': y10})
        except Exception: pass
    trows.sort(key=lambda r: r['Date'])
    save_csv(f'{D}/treasury.csv', trows, ['Date', 'Y2', 'Y10'])
    note(f"재무부 ok ~{trows[-1]['Date']}")
except Exception as e:
    trows = load_csv(f'{D}/treasury.csv'); note(f"재무부 실패: {e}")

# EPU — policyuncertainty.com 전 역사(1985~)
try:
    txt = get('https://www.policyuncertainty.com/media/All_Daily_Policy_Data.csv')
    epu_hist = []
    for r in csv.DictReader(io.StringIO(txt)):
        try: epu_hist.append((f"{int(r['year'])}-{int(r['month']):02d}-{int(r['day']):02d}", float(r['daily_policy_index'])))
        except Exception: pass
    save_csv(f'{D}/epu.csv', [{'Date': a, 'EPU': b} for a, b in epu_hist[-120:]], ['Date', 'EPU'])
    note(f"EPU ok ~{epu_hist[-1][0]} (역사 {len(epu_hist)}일)")
except Exception as e:
    epu_hist = [(r['Date'], float(r['EPU'])) for r in load_csv(f'{D}/epu.csv')]; note(f"EPU 실패: {e}")

# DIX/GEX — squeezemetrics 전 역사
try:
    txt = get('https://squeezemetrics.com/monitor/static/DIX.csv')
    dix_hist = []
    for r in csv.DictReader(io.StringIO(txt)):
        try: dix_hist.append((r['date'], float(r['dix']), float(r['gex'])))
        except Exception: pass
    save_csv(f'{D}/dix.csv', [{'Date': a, 'DIX': b, 'GEX': c} for a, b, c in dix_hist[-120:]], ['Date', 'DIX', 'GEX'])
    note(f"DIX ok ~{dix_hist[-1][0]}")
except Exception as e:
    dix_hist = [(r['Date'], float(r['DIX']), float(r['GEX'])) for r in load_csv(f'{D}/dix.csv')]; note(f"DIX 실패: {e}")

# COT — CFTC 공식(주간)
try:
    js = json.loads(get("https://publicreporting.cftc.gov/resource/gpe5-46if.json?cftc_contract_market_code=209742&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=4"))
    rows = load_csv(f'{D}/cot_tff_nq_lev.csv'); have = {r['report_date'] for r in rows}
    da = load_csv(f'{D}/cot_tff_nq_dealer_am.csv'); have2 = {r['report_date'] for r in da}
    for e in js:
        d0 = e['report_date_as_yyyy_mm_dd'][:10]
        if d0 not in have:
            rows.append({'report_date': d0, 'lev_long': e['lev_money_positions_long'], 'lev_short': e['lev_money_positions_short']})
        if d0 not in have2:
            da.append({'report_date': d0, 'dealer_long': e['dealer_positions_long_all'], 'dealer_short': e['dealer_positions_short_all'],
                       'am_long': e['asset_mgr_positions_long'], 'am_short': e['asset_mgr_positions_short']})
    rows.sort(key=lambda r: r['report_date']); da.sort(key=lambda r: r['report_date'])
    save_csv(f'{D}/cot_tff_nq_lev.csv', rows, ['report_date', 'lev_long', 'lev_short'])
    save_csv(f'{D}/cot_tff_nq_dealer_am.csv', da, ['report_date', 'dealer_long', 'dealer_short', 'am_long', 'am_short'])
    cot_rows = rows; note(f"COT ok ~{rows[-1]['report_date']}")
except Exception as e:
    cot_rows = load_csv(f'{D}/cot_tff_nq_lev.csv'); da = load_csv(f'{D}/cot_tff_nq_dealer_am.csv'); note(f"COT 실패: {e}")

# FINRA 메가캡 SVR
MEGA = {'AAPL', 'MSFT', 'NVDA', 'AMZN', 'TSLA', 'GOOGL', 'META'}
try:
    svr_rows = load_csv(f'{D}/svr_megacap.csv'); have = {r['date'] for r in svr_rows}
    added = 0
    for back in range(1, 15):
        t = date.today() - timedelta(days=back)
        if t.weekday() >= 5: continue
        iso = t.isoformat()
        if iso in have: continue
        try:
            txt = get(f'https://cdn.finra.org/equity/regsho/daily/CNMSshvol{iso.replace("-", "")}.txt')
            s = tot = 0.0
            for line in txt.splitlines():
                p = line.split('|')
                if len(p) >= 5 and p[1] in MEGA:
                    try: s += float(p[2]); tot += float(p[4])
                    except Exception: pass
            if tot > 0: svr_rows.append({'date': iso, 'svr': round(s / tot, 4)}); added += 1
        except Exception: pass
    svr_rows.sort(key=lambda r: r['date'])
    if added: save_csv(f'{D}/svr_megacap.csv', svr_rows, ['date', 'svr'])
    note(f"FINRA SVR +{added} ~{svr_rows[-1]['date'] if svr_rows else '없음'}")
except Exception as e:
    svr_rows = load_csv(f'{D}/svr_megacap.csv'); note(f"FINRA 실패: {e}")

# Equity P/C — Cboe CDN
try:
    pc_rows = load_csv(f'{D}/equity_pc_bydate.csv'); have = {r['date'] for r in pc_rows}
    added = 0
    for back in range(1, 15):
        t = date.today() - timedelta(days=back)
        if t.weekday() >= 5: continue
        iso = t.isoformat()
        if iso in have: continue
        try:
            j = json.loads(get(f'https://cdn.cboe.com/data/us/options/market_statistics/daily/{iso}_daily_options'))
            v = next((x['value'] for x in j.get('ratios', []) if x['name'] == 'EQUITY PUT/CALL RATIO'), None)
            if v: pc_rows.append({'date': iso, 'equity_pc': v, 'source': 'cdn.cboe.com'}); added += 1
        except Exception: pass
    pc_rows.sort(key=lambda r: r['date'])
    if added: save_csv(f'{D}/equity_pc_bydate.csv', pc_rows, ['date', 'equity_pc', 'source'])
    note(f"P/C +{added} ~{pc_rows[-1]['date']}")
except Exception as e:
    pc_rows = load_csv(f'{D}/equity_pc_bydate.csv'); note(f"P/C 실패: {e}")

# ============ 2. 계기 계산 (확정 산식 그대로) ============
ndx = {r['Date']: float(r['Close']) for r in ndx_rows}
dates = sorted(ndx); idx = {d0: i for i, d0 in enumerate(dates)}
vix = {r['Date']: float(r['Close']) for r in vix_rows}
svr = {r['date']: float(r['svr']) for r in svr_rows}
pc = {r['date']: float(r['equity_pc']) for r in pc_rows}
cot = sorted((r['report_date'], int(r['lev_short']) - int(r['lev_long'])) for r in cot_rows)
dealer = sorted((r['report_date'], int(r['dealer_long']) - int(r['dealer_short'])) for r in da)
tre = {r['Date']: (float(r['Y2']), float(r['Y10'])) for r in trows if r.get('Y2')}
ys = {n: {r['Date']: float(r['Close']) for r in ydata[n]} for n in ydata}

today = dates[-1]; i = idx[today]
ret1 = 100 * (ndx[today] / ndx[dates[i-1]] - 1)
cum3 = 100 * (ndx[today] / ndx[dates[i-3]] - 1) if i >= 3 else 0.0
event = abs(ret1) >= 2 or abs(cum3) >= 5
v = vix.get(today)
vk = sorted(vix)

def chgs(series, n=1):
    """일반 시계열의 최근 n스텝 변화(마지막 값 기준). 데이터 지연 허용 — 최신일 기준."""
    ks = sorted(series)
    if len(ks) < n + 1: return None, None
    return ks[-1], round(series[ks[-1]] - series[ks[-1-n]], 3)

def pctile(win, x): return round(100 * sum(1 for y in win if y <= x) / len(win))

def vix_1y(d0):
    if d0 not in vix: return None
    k = vk.index(d0)
    return None if k < 252 else pctile([vix[vk[j]] for j in range(k-251, k+1)], vix[d0])

def vix_jump(d0):
    if d0 not in vix: return None
    k = vk.index(d0)
    if k < 1: return None
    rels = [vix[vk[j]] / vix[vk[j-1]] - 1 for j in range(1, len(vk))]
    return round(100 * sum(1 for r in rels if r <= vix[d0] / vix[vk[k-1]] - 1) / len(rels), 1)

def smh_gap(d0):
    smh = ys.get('smh', {})
    k = idx.get(d0)
    if k is None or k < 1: return None
    p = dates[k-1]
    if d0 in smh and p in smh:
        return round(100 * (smh[d0] / smh[p] - 1) - 100 * (ndx[d0] / ndx[p] - 1), 2)
    return None

def nsp_f():
    if len(cot) < 52: return None
    return pctile([c[1] for c in cot[-52:]], cot[-1][1])

def svr_rel(d0):
    if d0 not in svr: return None
    sd = sorted(svr); k = sd.index(d0)
    return None if k < 20 else pctile([svr[sd[j]] for j in range(k-20, k)], svr[d0])

def dd60(d0):
    k = idx[d0]
    if k < 60: return None
    return round(100 * (ndx[d0] / max(ndx[dates[j]] for j in range(k-59, k+1)) - 1), 2)

def chg_of(series, d0, n):
    ks = sorted(series);
    if d0 not in series: return None
    k = ks.index(d0)
    return None if k < n else round(series[d0] - series[ks[k-n]], 3)

def pchg_of(series, d0, n=1):
    ks = sorted(series)
    if d0 not in series: return None
    k = ks.index(d0)
    return None if k < n else round(100 * (series[d0] / series[ks[k-n]] - 1), 2)

# 개별 계기
y2 = {d0: a for d0, (a, b) in tre.items()}; y10 = {d0: b for d0, (a, b) in tre.items()}
t_last = sorted(tre)[-1] if tre else None
epu_last = epu_hist[-1] if epu_hist else None
epu_pct = pctile([x for _, x in epu_hist], epu_last[1]) if epu_hist else None
dix_last = dix_hist[-1] if dix_hist else None
sr = svr_rel(sorted(svr)[-1]) if svr else None
pc_last = sorted(pc)[-1] if pc else None
np_ = nsp_f()
dn = dealer[-1][1] if dealer else None
sg = smh_gap(today)
v1y = vix_1y(today); vj = vix_jump(today)
move_d, move_c5 = (sorted(ys['move'])[-1], chg_of(ys['move'], sorted(ys['move'])[-1], 5)) if ys.get('move') else (None, None)

# ============ 3. 칸별 패널 ============
def gauge(name, val, read, state='na'):
    return {'name': name, 'val': val, 'read': read, 'state': state}
def st(cond_warn, cond_hot=False, missing=False):
    if missing: return 'na'
    return 'hot' if cond_hot else ('warn' if cond_warn else 'ok')

y2c1 = chg_of(y2, t_last, 1) if t_last else None
y2c5 = chg_of(y2, t_last, 5) if t_last else None
y10c1 = chg_of(y10, t_last, 1) if t_last else None
y10c5 = chg_of(y10, t_last, 5) if t_last else None
dxc1 = pchg_of(ys['dxy'], sorted(ys['dxy'])[-1]) if ys.get('dxy') else None
wtc1 = pchg_of(ys['wti'], sorted(ys['wti'])[-1]) if ys.get('wti') else None
drawdown = dd60(today)

# L2′ 탐사 게이지: MOVE × 커브(10Y−3M) 결합 백분위 (2020~ 전 역사 순위 곱, in-sample 탐사 등급)
mc_pct = None
try:
    mf = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/move_full.csv')}
    y3 = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/y3m.csv')}
    y10f = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/y10_full.csv')}
    def _asof(s, d0, back=10):
        y_, m_, dd_ = map(int, d0.split('-')); t_ = date(y_, m_, dd_)
        for b in range(back):
            k = (t_ - timedelta(days=b)).isoformat()
            if k in s: return s[k]
        return None
    hist = []
    for d0 in sorted(k for k in y10f if k >= '2020-01-01'):
        a = _asof(mf, d0); c1, c2 = y10f.get(d0), _asof(y3, d0)
        if a is not None and c1 is not None and c2 is not None:
            hist.append((d0, a, c1 - c2))
    if len(hist) > 300:
        mvv = [x[1] for x in hist]; cvv = [x[2] for x in hist]
        rm = sorted(mvv); rc = sorted(cvv)
        import bisect
        pr_m = [bisect.bisect_right(rm, v) / len(rm) for v in mvv]
        pr_c = [bisect.bisect_right(rc, v) / len(rc) for v in cvv]
        prod = [(pm - 0.5) * (pc - 0.5) for pm, pc in zip(pr_m, pr_c)]
        sp = sorted(prod)
        mc_pct = round(100 * bisect.bisect_right(sp, prod[-1]) / len(sp))
        note(f"MOVE×커브 게이지 ok ~{hist[-1][0]} 백분위 {mc_pct}")
except Exception as e:
    note(f"MOVE×커브 게이지 실패: {e}")

# ---- D칸 신용 조기경보 게이지 (32c, 관찰 등급 — 사전등록 전 판정 자산 아님) ----
bei_curve_v = None
try:
    _b5 = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/bei5y.csv')}
    _b10 = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/bei10y.csv')}
    _k5 = sorted(_b5); _k10 = sorted(_b10)
    if _k5 and _k10: bei_curve_v = round(_b10[_k10[-1]] - _b5[_k5[-1]], 2)
except Exception as e:
    note(f'BEI커브 게이지 실패: {e}')
cred_spread = None; cred_state = None; hy22 = None; ocr22 = None; bdc_r66 = None; clo_r22 = None; nfci_lev_v = None; lev_state = None; pff_r22 = None; jpy5 = None; jcot_pct = None
try:
    _jp = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/usdjpy.csv')}
    _jk = sorted(_jp)
    if len(_jk) > 6: jpy5 = round(100 * (_jp[_jk[-1]] / _jp[_jk[-5]] - 1), 2)
    _jc = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/jpy_cot.csv')}
    _ck = sorted(_jc)
    if len(_ck) > 60:
        _hist = [_jc[k] for k in _ck[-52:]]
        jcot_pct = round(100 * sum(1 for x in _hist if x <= _jc[_ck[-1]]) / len(_hist))
except Exception as e:
    note(f'캐리 게이지 실패: {e}')
try:
    _pf = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/pff.csv')}
    _lq = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/lqd.csv')}
    def _p22b(s):
        ks = sorted(s)
        return 100 * (s[ks[-1]] / s[ks[-16]] - 1) if len(ks) > 16 else None
    _x = _p22b(_pf); _y = _p22b(_lq)
    if _x is not None and _y is not None: pff_r22 = round(_x - _y, 2)
except Exception as e:
    note(f'PFF 게이지 실패: {e}')
try:
    _lv = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/nfci_lev.csv')}
    _cr2 = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/nfci_credit.csv')}
    _k1 = sorted(_lv); _k2 = sorted(_cr2)
    if _k1 and _k2:
        nfci_lev_v = round(_lv[_k1[-1]], 2)
        lev_state = '느슨한 신용+과잉 레버리지 (취약 배경)' if (nfci_lev_v >= 0.1 and _cr2[_k2[-1]] <= nfci_lev_v) else '보통'
except Exception as e:
    note(f'NFCI 게이지 실패: {e}')
try:
    _arc = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/arcc.csv')}
    _spq = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/spx.csv')}
    def _p66(s):
        ks = sorted(s)
        return 100 * (s[ks[-1]] / s[ks[-46]] - 1) if len(ks) > 46 else None
    _a = _p66(_arc); _s = _p66(_spq)
    if _a is not None and _s is not None: bdc_r66 = round(_a - _s, 1)
    _ja = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/jaaa.csv')}
    _jb = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/jbbb.csv')}
    def _p22(s):
        ks = sorted(s)
        return 100 * (s[ks[-1]] / s[ks[-16]] - 1) if len(ks) > 16 else None
    _x = _p22(_jb); _y = _p22(_ja)
    if _x is not None and _y is not None: clo_r22 = round(_x - _y, 2)
except Exception as e:
    note(f'BDC 게이지 실패: {e}')
try:
    _baa = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/baa_full.csv')}
    for r in load_csv(f'{D}/ext/baa.csv'): _baa[r['Date']] = float(r['Close'])
    _aaa = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/aaa_full.csv')}
    for r in load_csv(f'{D}/ext/aaa.csv'): _aaa[r['Date']] = float(r['Close'])
    _sp = {d0: _baa[d0] - _aaa[d0] for d0 in _baa if d0 in _aaa}
    _sd = sorted(_sp)
    if len(_sd) > 300:
        cred_spread = round(_sp[_sd[-1]], 2)
        _vals = [_sp[d0] for d0 in _sd]
        _ma200 = sum(_vals[-200:]) / 200
        _ma200_prev = sum(_vals[-266:-66]) / 200 if len(_vals) >= 266 else _ma200
        _med = sorted(_vals)[len(_vals) // 2]
        _slope = _ma200 - _ma200_prev
        if cred_spread > _ma200 and _slope > 0.02:
            cred_state = '초입 경보 (수준 낮음·상승 전환)' if cred_spread < _med else '전환 후기'
        else:
            cred_state = '비점등'
    _hy = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/hy_oas.csv')}
    _hs = sorted(_hy)
    if len(_hs) > 30: hy22 = round(_hy[_hs[-1]] - _hy[_hs[-16]], 2)
    _oc = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/ofr_credit.csv')}
    _os = sorted(_oc)
    if len(_os) > 30: ocr22 = round(_oc[_os[-1]] - _oc[_os[-16]], 2)
except Exception as e:
    note(f'신용 경보 게이지 실패: {e}')

# ---- B칸 분할 게이지 (2026-08-09 사용자 판정: B1 거시지표 / B2 침체공포) ----
cyc_gap = None; cu22v = None
try:
    _tre2 = {r['Date']: float(r['Y2']) for r in load_csv(f'{D}/treasury.csv') if r.get('Y2')}
    _efr2 = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/effr.csv')}
    def _asofP(s, d0, back=14):
        yy0, mm0, dd0 = map(int, d0.split('-')); t0 = date(yy0, mm0, dd0)
        for b in range(back):
            k = (t0 - timedelta(days=b)).isoformat()
            if k in s: return s[k]
        return None
    _a = _asofP(_tre2, today); _b = _asofP(_efr2, today)
    if _a is not None and _b is not None: cyc_gap = round(_a - _b, 2)
    _cop = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/copper.csv')}
    _gld = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/gold.csv')}
    _c1 = _asofP(_cop, today); _g1 = _asofP(_gld, today)
    _d30 = (date(*map(int, today.split('-'))) - timedelta(days=30)).isoformat()
    _c0 = _asofP(_cop, _d30); _g0 = _asofP(_gld, _d30)
    if all(x is not None for x in (_c1, _g1, _c0, _g0)) and _g1 and _g0:
        cu22v = round(100 * ((_c1 / _g1) / (_c0 / _g0) - 1), 1)
except Exception as e:
    note(f'B분할 게이지 실패: {e}')

# ---- D칸 위험 근접도 (32m — 직전 원형 문턱 동결, 관찰 등급) ----
prox_hits = None; prox_tot = None; prox_list = ''
try:
    _ld = lambda nm: {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/{nm}.csv')}
    def _last(s): ks = sorted(s); return s[ks[-1]] if ks else None
    def _chN(s, n):
        ks = sorted(s)
        return (s[ks[-1]] - s[ks[-n]]) if len(ks) > n else None
    _checks = []
    _lv2 = _ld('nfci_lev'); _cr3 = _ld('nfci_credit')
    _v = _last(_lv2); _c = _last(_cr3)
    if _v is not None: _checks.append(('레버', _v >= -0.08))
    if _v is not None and _c is not None: _checks.append(('G', (_c - _v) <= 0.05))
    _b2 = _ld('baa_full'); _a2 = _ld('aaa_full')
    try:
        for r in load_csv(f'{D}/ext/baa.csv'): _b2[r['Date']] = float(r['Close'])
        for r in load_csv(f'{D}/ext/aaa.csv'): _a2[r['Date']] = float(r['Close'])
    except Exception: pass
    _spd = {d0: _b2[d0] - _a2[d0] for d0 in _b2 if d0 in _a2}
    _s66 = _chN(_spd, 45)
    if _s66 is not None: _checks.append(('스프66Δ', _s66 >= -0.03))
    _o22 = _chN(_ld('ofr_credit'), 16)
    if _o22 is not None: _checks.append(('OFR22Δ', _o22 >= 0.01))
    if bdc_r66 is not None: _checks.append(('BDC', bdc_r66 <= -2.96))
    if pff_r22 is not None: _checks.append(('PFF', pff_r22 <= -0.14))
    if jcot_pct is not None: _checks.append(('엔COT', jcot_pct <= 41.35))
    _cp2 = _ld('cp3m'); _tb2 = _ld('tb3m')
    _x = _last(_cp2); _y = _last(_tb2)
    if _x is not None and _y is not None: _checks.append(('CP', (_x - _y) >= 0.14))
    _dp = _ld('deposits'); _dk = sorted(_dp)
    if len(_dk) > 14: _checks.append(('예금', 100 * (_dp[_dk[-1]] / _dp[_dk[-14]] - 1) <= 1.36))
    _mvv = move_d and ys['move'][move_d]
    if _mvv: _checks.append(('MOVE', _mvv >= 79.16))
    prox_hits = sum(1 for _, b in _checks if b); prox_tot = len(_checks)
    prox_list = '·'.join(n for n, b in _checks if b)
    _wood_names = {'레버', 'G', '엔COT', '예금'}
    prox_wood = sum(1 for n, b in _checks if b and n in _wood_names)
    prox_fire = sum(1 for n, b in _checks if b and n not in _wood_names)
    prox_wt = sum(1 for n, _ in _checks if n in _wood_names)
    prox_ft = sum(1 for n, _ in _checks if n not in _wood_names)
except Exception as e:
    note(f'근접도 실패: {e}')

panels = {
 'A': {'title': 'A 통화정책 (86건)', 'grammar': '서프라이즈 용량 반응 — 2Y 당일 변화가 출력과 단조(#019). 큰 |2Y당일|이 곧 사건 용량.',
   'g': [gauge('2Y 당일 변화(pp)', y2c1, '±0.10 이상이면 서프라이즈 급', st(y2c1 is not None and abs(y2c1) >= 0.10, missing=y2c1 is None)),
         gauge('2Y 5일 변화(pp)', y2c5, '누적 재가격 방향', 'na' if y2c5 is None else 'ok')]},
 'B1': {'title': 'B1 거시지표 (45건)', 'grammar': '사이클 지배(카드11) — 지표 내용(핫/쿨/호조/둔화)보다 사이클 위치가 1개월을 결정. 긴축기 상승사건 전패급. 10일 체크포인트 정합 89%. 2022 적재 명기, 홀드아웃 카운트 중(P8 포함).',
   'g': [gauge('Y2−EFFR 사이클 게이지(pp)', cyc_gap, '≤+0.25 완화·종점(지표 사건 우호) / >+0.25 긴축(경계)', st(cyc_gap is not None and cyc_gap > 0.25, missing=cyc_gap is None)),
         gauge('2Y 당일 변화(pp)', y2c1, 'A칸과 계기 공유', 'na' if y2c1 is None else 'ok')]},
 'B2': {'title': 'B2 침체공포 (23건)', 'grammar': '재가격 문법 — 사이클 규칙 무효(오히려 역행: 긴축기에 기준선보다 우위). 구리/금 급락 후 사건=항복 국면(카드8), L3 부호 반전(+0.45 역독).',
   'g': [gauge('구리/금 22일 변화(%)', cu22v, '큰 음수(이미 급락)면 침체 재가격 완료 — 사건 시 반등 우세', 'na' if cu22v is None else 'ok'),
         gauge('10Y 당일 변화(pp)', y10c1, '장기 재가격', 'na' if y10c1 is None else 'ok')]},
 'C': {'title': 'C 지정학·충격 (78건)', 'grammar': '에너지·통화형=달러 용량계(상관 0.69)·유가 보조. 보건형=VIX 경로만. 방향 비대칭: 고조 달러 0.57 / 완화 방출 0.33(#080).',
   'g': [gauge('달러 당일 %', dxc1, '급등=고조 용량', st(dxc1 is not None and abs(dxc1) >= 0.5, missing=dxc1 is None)),
         gauge('WTI 당일 %', wtc1, '보조 용량계', st(wtc1 is not None and abs(wtc1) >= 3, missing=wtc1 is None)),
         gauge('EPU 역사 백분위', epu_pct, '정책·확전 소음 수준', st(epu_pct is not None and epu_pct >= 90, missing=epu_pct is None))]},
 'D': {'title': 'D 신용·시스템 (5건)', 'grammar': '표본 5건 — 식 검증 불가. 봉인 관찰(32단계): 2020년대 신용 사건 5/5가 한 달 양수(정책 즉시 개입 시대), 항복 축은 VIX가 아니라 MOVE·OFR(SVB는 VIX 23에 MOVE 170), 저VIX 경고·10일 체크포인트 이식 불가.',
   'g': [gauge('MOVE', move_d and ys['move'][move_d], '채권 발작 온도계', st(move_d is not None and ys['move'][move_d] >= 120, missing=move_d is None)),
         gauge('MOVE 5일 변화', move_c5, '급등=시스템 경계', 'na' if move_c5 is None else 'ok'),
         gauge('신용 스프레드 (BAA−AAA)', cred_spread, f'사이클 상태: {cred_state or "계산 불가"} — 방향성 관찰 전용(32f 유의 미달 9%·에피소드 의존): 저점 상승 전환의 역사 명중 2000·01·18·22 / 실패 1998·2005', 'na' if cred_spread is None else ('warn' if (cred_state or '').startswith('초입') else 'ok')),
         gauge('하이일드 스프레드 22일 변화(pp)', hy22, '급확대=신용 재가격 진행(②③상한 경계) — 사모신용 사각 주의', st(hy22 is not None and hy22 >= 0.4, missing=hy22 is None)),
         gauge('OFR 신용 서브지수 22일 변화', ocr22, '+0.3 이상이며 VIX 평온하면 2007형 괴리 관찰', st(ocr22 is not None and ocr22 >= 0.3, missing=ocr22 is None)),
         gauge('BDC 상대강도 66일 (ARCC−SPX, %p)', bdc_r66, '사모신용의 공개 시가평가 — 감별 전용(타이밍 예측 기각): ≤−8이면 사모신용 스트레스 진행. 신용 사건 시 진원지 판독(2007·2015·2025 점등 / GE·헝다 비점등)', st(bdc_r66 is not None and bdc_r66 <= -8, missing=bdc_r66 is None)),
         gauge('CLO 메자닌 상대 22일 (JBBB−JAAA, %p)', clo_r22, '레버리지론 하위등급 약세 = 사모신용 인접 스트레스 (감별 보조)', st(clo_r22 is not None and clo_r22 <= -1, missing=clo_r22 is None)),
         gauge('위험 2층: 장작(배경)×불씨(발화)', prox_hits is not None and f'장작 {prox_wood}/{prox_wt} · 불씨 {prox_fire}/{prox_ft}', f'현황판 전용(32p: 단순 MOVE·VIX 조합보다 못함 — 꼬리 농축 주장 철회, 정정 8호). 장작=느린 취약성 / 불씨=빠른 재가격의 분해 표시. 유일 생존 단서는 순서 조건(장작 60일 선행 후 발화 — 탐사, P9 대기). 점등: {prox_list or "없음"}', st(prox_hits is not None and prox_wood >= 3 and prox_fire >= 3, missing=prox_hits is None)),
         gauge('엔 캐리 게이지 (USDJPY 5일 %)', jpy5, '큰 음수=엔 급등=캐리 청산 진행(동시·감별 전용, 상시 예측 실격). 항복 동반(VIX≥25) 시 3개월 +16.2%/83% — 역사적 매수 구간. 저VIX의 조용한 엔 급등은 고점 경고 단서(2000·2002·2023-07)', st(jpy5 is not None and jpy5 <= -3, missing=jpy5 is None)),
         gauge('엔 투기 포지션 52주 백분위', jcot_pct, '≤10 순숏 극단=캐리 취약 배경(타이밍 아닌 규모 증폭 조건 — 2024-07 백분위 1% 한 달 뒤 대청산)', st(jcot_pct is not None and jcot_pct <= 10, missing=jcot_pct is None)),
         gauge('은행 우선주 상대 22일 (PFF−LQD, %p)', pff_r22, '은행판 강도 감별(사건 시 전용, 상시 무정보): ≤−10 시스템급(2008형) / −5~0 국지 / 양수 무해(NYCB형)', st(pff_r22 is not None and pff_r22 <= -5, missing=pff_r22 is None)),
         gauge('NFCI 레버리지 서브지수', nfci_lev_v, f'방향성 관찰 전용(32f 블록 플라시보 유의 미달 11%): {lev_state or "?"} — 역사적으로 이 배경의 3개월이 기준선보다 약했음(우연 범위와 겹침). 국면 게이지와 독립축인 점만 확정', st(nfci_lev_v is not None and (lev_state or '').startswith('느슨'), missing=nfci_lev_v is None))]},
 'E': {'title': 'E 빅테크 실적·섹터 (51건)', 'grammar': '카드14(33단계): 실적 사건의 1개월은 실적이 아니라 인플레 제약 환경이 결정. 긴축기엔 실적 랠리도 fake(38%). 시즌 첫 사건만 정보. 긴축기 1개월 랠리는 2개월째 역전(14%).',
   'g': [gauge('smh_gap (SMH−NDX)', sg, '큰 음수=반도체발. 동시 판독 전용(전조 아님)', st(sg is not None and abs(sg) >= 2, cond_hot=sg is not None and sg <= -3, missing=sg is None)),
         gauge('BEI 커브 (10Y−5Y 기대인플레, pp)', bei_curve_v, '역전(≤0)=인플레 제약 극심 — 실적 사건 시 1개월 열위 15%(카드14 1단, P10 카운트). 정상권(+0.1↑)은 무정보 — 판정 금지', st(bei_curve_v is not None and bei_curve_v <= 0, missing=bei_curve_v is None))]},
 'F': {'title': 'F 무역·재정·정책 (44건)', 'grammar': '관세: EPU는 시대상수(#015) — 판별은 GEX·성장·시스템(#013). FISCAL: 점화 단조 1.65→4.50%.',
   'g': [gauge('EPU 역사 백분위', epu_pct, 'C칸과 공유 — 수준보다 급변에 주목', 'na' if epu_pct is None else 'ok'),
         gauge('GEX($B)', dix_last and round(dix_last[2] / 1e9, 1), '음수=딜러 숏감마(증폭 국면)', st(dix_last is not None and dix_last[2] < 0, missing=dix_last is None))]},
 'G': {'title': 'G 기술적·수급 (21건)', 'grammar': '판정식은 사후 분류 전용(#070). 모멘텀=SVR≤20+P/C≤0.55 / S1 스퀴즈=SVR≥80+NSP≥80.',
   'g': [gauge('SVR 20일 상대', sr, '≤20 저공매도 / ≥80 숏 과밀(#049)', st(sr is not None and (sr <= 20 or sr >= 80), missing=sr is None)),
         gauge('Equity P/C', pc.get(pc_last), '≤0.55 과열 낙관(#066)', st(pc_last is not None and pc[pc_last] <= 0.55, missing=pc_last is None)),
         gauge('NSP 52주 백분위', np_, '≥80 과밀 — 딜러 순숏 국면에선 휴면(#082)', 'na' if np_ is None else ('warn' if (np_ >= 80 and dn is not None and dn > 0) else 'ok')),
         gauge('딜러 순포지션', dn, '양수 복귀=NSP 부활 조건(#082)', st(dn is not None and dn < 0, missing=dn is None)),
         gauge('DIX', dix_last and dix_last[1], '높을수록 다크풀 매집', 'na' if dix_last is None else 'ok')]},
 'REGIME': {'title': '공통 국면', 'grammar': 'VER #001: VIX≥28 하락 사건 → 1개월 반등 84~94%(국면 첫날 68%). 저VIX 캄 크래시 사각(#084)은 아래 탐사 게이지가 감시.',
   'g': [gauge('VIX', v, '≥28 국면 / ≥35 확진(#072)', st(v is not None and v >= 28, cond_hot=v is not None and v >= 35, missing=v is None)),
         gauge('VIX 1년 백분위', v1y, '≥90 상대 공포 국면(#083)', st(v1y is not None and v1y >= 90, missing=v1y is None)),
         gauge('VIX 상대급변 백분위', vj, '≥95 급변(#083 A2)', st(vj is not None and vj >= 95, missing=vj is None)),
         gauge('60일 고점 대비(%)', drawdown, '깊을수록 항복 국면(#033)', st(drawdown is not None and drawdown <= -10, missing=drawdown is None)),
         gauge('MOVE×커브 결합백분위 [탐사]', mc_pct,
               '저VIX 체제 전용 하방 게이지(L2′, in-sample) — ≥90이면 향후 1개월 경계. VIX≥28 국면에선 무효',
               'na' if mc_pct is None else ('warn' if (mc_pct >= 90 and (v is None or v < 28)) else 'ok'))]},
}

# ============ 4. 1개월 판정 (보유 자산만으로 — 없으면 없다고 명시) ============
if event and ret1 < 0:
    if v is not None and v >= 28:
        first_day = vix.get(dates[i-1], 99) < 28
        monthly = {'stance': 'BOUNCE', 'label': '반등 우세',
                   'why': f'VER #001: VIX {v:.1f} ≥28 하락 사건 — 1개월 반등 84~94%' + (' (단, 국면 첫날이라 68% — 즉시 매수 금지 각주 적용)' if first_day else '')}
    else:
        monthly = {'stance': 'NO_CALL', 'label': '판정 불가 — 무전조 구간',
                   'why': f'하락 사건이나 VIX {v if v else "결측"} < 28 — 유일한 1개월 자산(VER #001) 적용 밖. #084 사각. 어떤 등급 자산도 방향을 주장하지 않음.'}
elif event:
    monthly = {'stance': 'NO_ASSET', 'label': '상승 사건 — 1개월 자산 없음',
               'why': 'G 판정식은 원인 분류 전용(#070). 상승 사건의 1개월 예측 자산은 미보유.'}
else:
    monthly = {'stance': 'NONE', 'label': '무사건 — 판정 없음',
               'why': '사건 문턱(±2%/3일 ±5%) 미달. 칸별 계기는 상황 파악용.'}

# ============ 5. 출력·채점·푸시 ============
status = {
 'updated_utc': datetime.utcnow().isoformat() + 'Z', 'date': today,
 'ndx': ndx[today], 'ret1_pct': round(ret1, 2), 'cum3_pct': round(cum3, 2),
 'event': event, 'vix': v, 'monthly': monthly, 'panels': panels, 'collect_log': log,
 'event_cards': [],
}
with open(f'{D}/status.json', 'w', encoding='utf-8') as f:
    json.dump(status, f, ensure_ascii=False, indent=1)

sc_path = f'{D}/daily_scorecard.csv'
sc = load_csv(sc_path)
if not any(r['date'] == today for r in sc):
    sc.append({'date': today, 'ret1': round(ret1, 2), 'cum3': round(cum3, 2), 'event': int(event),
               'vix': v, 'vix_1y_pct': v1y, 'smh_gap': sg, 'nsp': np_, 'svr_rel': sr,
               'pc': pc.get(pc_last), 'dealer_net': dn, 'y2_chg1': y2c1, 'dxy_chg1': dxc1,
               'move': move_d and ys['move'][move_d], 'epu_pct': epu_pct,
               'gex_b': dix_last and round(dix_last[2] / 1e9, 1), 'drawdown60': drawdown,
               'move_curve_pct': mc_pct,
               'wood': prox_wood if 'prox_wood' in dir() else '', 'fire': prox_fire if 'prox_fire' in dir() else '',
               'jpy5': jpy5, 'jcot_pct': jcot_pct})
    save_csv(sc_path, sc, list(sc[-1].keys()))

# ==== 대장 자동 채점기 (매일 실행 — 만기 도래분 fwd22 자동 기입) ====
try:
    led_p = f'{D}/forward_count_ledger.csv'
    led = load_csv(led_p)
    nd_sorted = sorted(ndx); nd_pos = {d0: i for i, d0 in enumerate(nd_sorted)}
    changed = False
    for row_ in led:
        if row_.get('fwd22_actual'): continue
        if row_.get('card') == 'P8_OBS20': continue
        ev_d = row_.get('date', '')
        i0 = nd_pos.get(ev_d)
        if i0 is not None and i0 + 22 < len(nd_sorted):
            f22v = round(100 * (ndx[nd_sorted[i0 + 22]] / ndx[nd_sorted[i0]] - 1), 2)
            row_['fwd22_actual'] = f22v; changed = True
            note(f'대장 채점: {ev_d} fwd22 {f22v:+.2f}%')
    if changed:
        save_csv(led_p, led, list(led[0].keys()))
except Exception as e:
    note(f'대장 채점 실패: {e}')

# ==== P8 OBS-20 2단 순차 판정 (사전등록 2026-08-09 — 산식 동결) ====
# m1 만기(+22거래일): CYC(Y2-EFFR)로 국면 분류 + 셀 판정 행 생성
# m2 만기(+44거래일): 22~44일 수익 자동 채점 (긴축 랠리→열위 / 긴축 하락→반등 / 완화→우호)
try:
    led_p = f'{D}/forward_count_ledger.csv'
    led = load_csv(led_p)
    nd_sorted = sorted(ndx); nd_pos = {d0: i for i, d0 in enumerate(nd_sorted)}
    tre = {r['Date']: float(r['Y2']) for r in load_csv(f'{D}/treasury.csv') if r.get('Y2')}
    efr = {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/effr.csv')}
    def _asof2(s, d0, back=12):
        yy0, mm0, dd0 = map(int, d0.split('-')); t0 = date(yy0, mm0, dd0)
        for b in range(back):
            k = (t0 - timedelta(days=b)).isoformat()
            if k in s: return s[k]
        return None
    changed = False
    for ev_d in sorted({r['date'] for r in led if r.get('card') != 'P8_OBS20'}):
        i0 = nd_pos.get(ev_d)
        if i0 is None: continue
        if any(r.get('card') == 'P8_OBS20' and r.get('date') == ev_d for r in led): continue
        if i0 + 22 < len(nd_sorted):
            m1v = 100 * (ndx[nd_sorted[i0 + 22]] / ndx[nd_sorted[i0]] - 1)
            y2v = _asof2(tre, ev_d); efv = _asof2(efr, ev_d)
            if y2v is None or efv is None: continue
            cycv = y2v - efv
            if cycv > 0.25:
                verdict = ('긴축∩랠리 → 2개월째 열위 예측' if m1v > 0 else '긴축∩하락소화 → 2개월째 반등 예측')
            else:
                verdict = '완화기 → 2개월째 우호 예측 (분류 불문)'
            yy0, mm0, dd0 = map(int, ev_d.split('-'))
            led.append({'date': ev_d, 'event_ret': '', 'domain': 'B칸 태깅분만 카운트(그 외 참고)',
                        'card': 'P8_OBS20', 'reading': f'm1 {m1v:+.2f}% · CYC {cycv:+.2f}',
                        'verdict': verdict,
                        'fwd22_due': (date(yy0, mm0, dd0) + timedelta(days=64)).isoformat(),
                        'fwd22_actual': '', 'pass': ''})
            note(f'P8 OBS-20 분류: {ev_d} m1 {m1v:+.2f}% CYC {cycv:+.2f}')
            changed = True
    for r in led:
        if r.get('card') != 'P8_OBS20' or r.get('fwd22_actual'): continue
        i0 = nd_pos.get(r.get('date', ''))
        if i0 is not None and i0 + 44 < len(nd_sorted):
            m2v = round(100 * (ndx[nd_sorted[i0 + 44]] / ndx[nd_sorted[i0 + 22]] - 1), 2)
            r['fwd22_actual'] = m2v
            want_neg = ('열위' in r.get('verdict', ''))
            r['pass'] = 'Y' if ((m2v < 0) == want_neg) else 'N'
            note(f'P8 OBS-20 채점: {r.get("date")} m2 {m2v:+.2f}% {r["pass"]}')
            changed = True
    if changed:
        save_csv(led_p, led, ['date', 'event_ret', 'domain', 'card', 'reading', 'verdict', 'fwd22_due', 'fwd22_actual', 'pass'])
except Exception as e:
    note(f'P8 처리 실패: {e}')

# ==== P9 자동 적립·채점 (사전등록 2026-08-12 — 산식 동결) ====
# P9a: 굳은 장작(60일 중 40일+ 장작≥3) 위의 불씨≥3 → 적립, 66일 내 최대낙폭 ≥15% 채점
# P9c: JPY5 ≤ −3% AND VIX ≥ 25 → 적립, fwd66 양수 채점
try:
    led_p = f'{D}/forward_count_ledger.csv'
    led = load_csv(led_p)
    nd_sorted = sorted(ndx); nd_pos = {d0: i for i, d0 in enumerate(nd_sorted)}
    changed = False
    def _recent(card, days_):
        yy0, mm0, dd0 = map(int, today.split('-'))
        cut = (date(yy0, mm0, dd0) - timedelta(days=days_)).isoformat()
        return any(r.get('card') == card and r.get('date', '') >= cut for r in led)
    # P9a 적립
    try:
        sc_rows = load_csv(f'{D}/daily_scorecard.csv')
        hist = [r for r in sc_rows if r.get('wood') not in (None, '', 'None')][-60:]
        if len(hist) >= 60:
            wd_days = sum(1 for r in hist if float(r['wood']) >= 3)
            cur_fire = float(hist[-1]['fire']) if hist[-1].get('fire') not in ('', None) else 0
            if wd_days >= 40 and cur_fire >= 3 and not _recent('P9a_G', 56):
                yy0, mm0, dd0 = map(int, today.split('-'))
                led.append({'date': today, 'event_ret': '', 'domain': 'D칸 상시(P9a)', 'card': 'P9a_G',
                            'reading': f'장작지속 {wd_days}/60 · 불씨 {cur_fire:.0f}/6',
                            'verdict': '66일 내 최대낙폭 ≥15% 예측 (합격선: 첫 5건 중 2+)',
                            'fwd22_due': (date(yy0, mm0, dd0) + timedelta(days=96)).isoformat(),
                            'fwd22_actual': '', 'pass': ''})
                note(f'P9a 적립: 장작 {wd_days}/60 불씨 {cur_fire:.0f}')
                changed = True
    except Exception as e:
        note(f'P9a 적립 실패: {e}')
    # P9c 적립
    if jpy5 is not None and v is not None and jpy5 <= -3 and v >= 25 and not _recent('P9c_CARRY', 56):
        yy0, mm0, dd0 = map(int, today.split('-'))
        led.append({'date': today, 'event_ret': '', 'domain': 'D칸 상시(P9c)', 'card': 'P9c_CARRY',
                    'reading': f'JPY5 {jpy5:+.2f}% · VIX {v:.1f}',
                    'verdict': 'fwd66 양수 예측 — 캐리 항복 매수 (합격선: 첫 8건 중 6+)',
                    'fwd22_due': (date(yy0, mm0, dd0) + timedelta(days=96)).isoformat(),
                    'fwd22_actual': '', 'pass': ''})
        note(f'P9c 적립: JPY5 {jpy5:+.2f} VIX {v:.1f}')
        changed = True
    # 채점 (66거래일 만기)
    for r in led:
        if r.get('card') not in ('P9a_G', 'P9c_CARRY') or r.get('fwd22_actual'): continue
        i0 = nd_pos.get(r.get('date', ''))
        if i0 is None or i0 + 66 >= len(nd_sorted): continue
        if r['card'] == 'P9c_CARRY':
            f66v = round(100 * (ndx[nd_sorted[i0 + 66]] / ndx[nd_sorted[i0]] - 1), 2)
            r['fwd22_actual'] = f66v; r['pass'] = 'Y' if f66v > 0 else 'N'
        else:
            wvals = [ndx[nd_sorted[j]] for j in range(i0, i0 + 67)]
            peak = wvals[0]; mdd_v = 0.0
            for x in wvals:
                peak = max(peak, x); mdd_v = min(mdd_v, 100 * (x / peak - 1))
            r['fwd22_actual'] = round(mdd_v, 2); r['pass'] = 'Y' if mdd_v <= -15 else 'N'
        note(f"{r['card']} 채점: {r['date']} → {r['fwd22_actual']} {r['pass']}")
        changed = True
    if changed:
        save_csv(led_p, led, ['date', 'event_ret', 'domain', 'card', 'reading', 'verdict', 'fwd22_due', 'fwd22_actual', 'pass'])
except Exception as e:
    note(f'P9 처리 실패: {e}')

msg = None
if event:
    # ==== 사전등록 카드 판독 (preregistration_20260808.md — 산식 동결) ====
    cards = []
    try:
        def _pctile_of(series_dict, x, since='2015-01-01'):
            vals = [v for k, v in series_dict.items() if k >= since]
            return round(100 * sum(1 for v in vals if v <= x) / max(len(vals), 1)) if vals else None
        exd = lambda nm: {r['Date']: float(r['Close']) for r in load_csv(f'{D}/ext/{nm}.csv')}
        mvf = exd('move_full'); y10h = exd('y10_hist'); y3h = exd('y3m_hist'); v9 = exd('vix9d')
        cpf = exd('copper'); gdf = exd('gold')
        def _asof(s, d0, back=28):  # MOVE 등 지연 게시 원천 대비 소급 4주
            y_, m_, dd_ = map(int, d0.split('-')); t_ = date(y_, m_, dd_)
            for b in range(back):
                k = (t_ - timedelta(days=b)).isoformat()
                if k in s: return s[k]
            return None
        M_ = _asof(mvf, today); C_ = (y10h.get(today) or _asof(y10h, today))
        C3 = _asof(y3h, today); T9v = None
        v9v = _asof(v9, today)
        if v9v and v: T9v = v9v / v
        curve_v = (C_ - C3) if (C_ is not None and C3 is not None) else None
        mp = _pctile_of(mvf, M_) if M_ else None
        cp2 = _pctile_of({k: y10h[k] - y3h[k] for k in y10h if k in y3h}, curve_v) if curve_v is not None else None
        t9p = _pctile_of({k: v9[k] / vix[k] for k in v9 if k in vix and vix[k]}, T9v) if T9v else None
        vp = _pctile_of(vix, v) if v else None
        cg_ = {k: cpf[k] / gdf[k] for k in cpf if k in gdf}
        a_ = _asof(cg_, today); yy_, mm_, dd2 = map(int, today.split('-'))
        b_ = _asof(cg_, (date(yy_, mm_, dd2) - timedelta(days=30)).isoformat())
        cu22 = round(100 * (a_ / b_ - 1), 2) if (a_ and b_) else None
        cu22p = _pctile_of({k: 0 for k in []}, 0) if cu22 is None else None
        L1v = min(vp, epu_pct) if (vp is not None and epu_pct is not None) else None
        L1p_v = (L1v - min(mp, cp2)) if (L1v is not None and mp is not None and cp2 is not None) else None
        L3v = (min(mp, cp2) - t9p) if (mp is not None and cp2 is not None and t9p is not None) else None
        # v0.5 확장: L4·L6·R2·종합점수 (카드집 v1.8 전면 구현)
        skf = exd('skew'); sk_ = _asof(skf, today)
        skp = _pctile_of(skf, sk_) if sk_ else None
        L4v = min(mp, skp) if (mp is not None and skp is not None) else None
        r10 = exd('real10y'); ef_ = exd('effr')
        y10t = _asof(y10h, today); r10t = _asof(r10, today)
        bei_t = (y10t - r10t) if (y10t is not None and r10t is not None) else None
        y10p7 = _asof(y10h, (date(yy_, mm_, dd2) - timedelta(days=7)).isoformat())
        r10p7 = _asof(r10, (date(yy_, mm_, dd2) - timedelta(days=7)).isoformat())
        bei5 = round(bei_t - (y10p7 - r10p7), 3) if (bei_t is not None and y10p7 is not None and r10p7 is not None) else None
        y10prev = _asof(y10h, (date(yy_, mm_, dd2) - timedelta(days=4)).isoformat())
        y10c1 = round(abs(y10t - y10prev), 3) if (y10t is not None and y10prev is not None) else None
        L6_ok = (bei5 is not None and bei5 >= 0.05) or (y10c1 is not None and y10c1 >= 0.08)
        # R2: 사이클 게이지 + 로테이션 감별
        y2t = None
        try:
            trec = load_csv(f'{D}/treasury.csv')
            y2t = float([r_ for r_ in trec if r_.get('Y2')][-1]['Y2'])
        except Exception: pass
        eft = _asof(ef_, today)
        cyc = round(y2t - eft, 2) if (y2t is not None and eft is not None) else None
        qqe = exd('qqqe'); qq = exd('qqq')
        qgap = None
        try:
            prevd = (date(yy_, mm_, dd2) - timedelta(days=4)).isoformat()
            qgap = round(100 * (_asof(qqe, today) / _asof(qqe, prevd) - 1) - 100 * (_asof(qq, today) / _asof(qq, prevd) - 1), 2)
        except Exception: pass
        rot = (qgap is not None and qgap > 1) and (sg is not None and sg < 0)
        score = None
        if all(x is not None for x in [L1v, t9p, mp, cp2]):
            score = L1v + t9p - min(mp, cp2)
        # 카드 문안 — 칸별 조건부 시나리오 (태깅 전 병렬 표시)
        cards = [
            f"[상시] L2' 결합백분위 {mc_pct} → {'위험' if (mc_pct or 0) >= 80 else ('안전' if (mc_pct or 99) <= 20 else '중립')} (저VIX 하락 전용)",
            f"[전칸] L1 항복점수 min(VIX백분위 {vp}, EPU백분위 {epu_pct}) = {L1v} → {'항복(반등 우세)' if (L1v or 0) >= 80 else '미달'} | L1' {L1p_v} | 종합회복점수 {score}",
            f"[IF A칸·매파] L3 = min(MOVE {mp}, 커브 {cp2}) − T9 {t9p} = {L3v} → {'위험(회복 열위)' if (L3v or -99) > 30 else '중립·회복 여지'} ※매파 상승이면 R1(되돌림 우세)",
            f"[IF A칸·비둘기] L6 채권재가격: BEI5 {bei5}pp·|Y10c1| {y10c1}pp → {'재가격 실재(지속 우세)' if L6_ok else '무반응 말잔치(fake 경계)'}",
            f"[IF B1·물가둔화] R2: Y2−EFFR {cyc} → {'종점·인하기(지속 우세)' if (cyc is not None and cyc <= 0.25) else '긴축기(fake 경계)'} | 로테이션(QQQE_gap {qgap}·smh_gap {sg}) → {'⚠fake 감별 발동' if rot else '정상'}",
            f"[IF B2·침체공포] L3 부호 반전 주의: 채권 스트레스=가격 완료=반등 신호 (L3값 {L3v} 역독)",
            f"[IF F칸·정책] L5 구리/금 22일 {cu22}% → {'재가격 완료(반등 우세)' if (cu22 is not None and cu22 < -3) else ('미완(정체 경계)' if (cu22 is not None and cu22 > 3) else '중립')}",
            f"[IF A칸 공통] L4 = min(MOVE {mp}, SKEW백분위 {skp}) = {L4v} (2023~ 약화 — 참고)",
            f"[체크포인트] 10거래일 뒤 사건일 종가 대비 음수면 fake 분류 확정 (대장 자동 채점)",
        ]
        led_p = f'{D}/forward_count_ledger.csv'
        led = load_csv(led_p)
        if not any(r.get('date') == today for r in led):
            led.append({'date': today, 'event_ret': round(ret1, 2), 'domain': ('사각지대' if (v or 99) < 28 and ret1 < 0 else ('하락' if ret1 < 0 else '상승')),
                        'card': 'AUTO', 'reading': ' | '.join(cards), 'verdict': '태깅·만기 대기',
                        'fwd22_due': (date(yy_, mm_, dd2) + timedelta(days=32)).isoformat(), 'fwd22_actual': '', 'pass': ''})
            save_csv(led_p, led, ['date', 'event_ret', 'domain', 'card', 'reading', 'verdict', 'fwd22_due', 'fwd22_actual', 'pass'])
        note('사전등록 카드 판독·대장 기입 완료')
    except Exception as e:
        note(f'카드 판독 실패: {e}')
    hot = [f"{p['title']}: " + ', '.join(f"{g['name']}={g['val']}" for g in panels[k]['g'] if g['state'] in ('warn', 'hot'))
           for k, p in panels.items() if any(g['state'] in ('warn', 'hot') for g in p['g'])]
    msg = '\n'.join([f"[사건] {today} NDX {ret1:+.2f}% (3일 {cum3:+.2f}%)",
                     f"1개월 판정: {monthly['label']} — {monthly['why']}",
                     '경계 계기: ' + ('; '.join(hot) if hot else '전 계기 중립 (무전조형)'),
                     '── 사전등록 카드 판독 ──'] + cards +
                    ['다음: 세션 열어 루프 A 태깅 (원인 확정은 리서치로)'])
    with open(f'{D}/briefing_{today}.md', 'w', encoding='utf-8') as f:
        f.write(msg)
    # status.json에 카드 반영 (재기록)
    try:
        status['event_cards'] = cards
        with open(f'{D}/status.json', 'w', encoding='utf-8') as f:
            json.dump(status, f, ensure_ascii=False, indent=1)
    except Exception as e:
        note(f'status 카드 재기록 실패: {e}')
elif v is not None and v >= 35:
    msg = f"[확진] {today} VIX {v} ≥35 — 10일 내 사건 사실상 확정(#072)"
elif v1y is not None and v1y >= 90:
    msg = f"[국면] {today} VIX 1년 백분위 {v1y} — 상대 공포 국면(#083)"

if msg and NTFY:
    try:
        req = urllib.request.Request(f'https://ntfy.sh/{NTFY}', data=msg.encode('utf-8'),
                                     headers={'Title': 'NDX watch', 'Priority': 'high' if event else 'default'})
        urllib.request.urlopen(req, timeout=15)
        note('ntfy 푸시 발송')
    except Exception as e:
        note(f'ntfy 실패: {e}')

note(f"완료: {today} ret {ret1:+.2f}% event={event} 판정={monthly['label']}")
