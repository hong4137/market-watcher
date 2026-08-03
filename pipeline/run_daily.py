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

panels = {
 'A': {'title': 'A 통화정책 (86건)', 'grammar': '서프라이즈 용량 반응 — 2Y 당일 변화가 출력과 단조(#019). 큰 |2Y당일|이 곧 사건 용량.',
   'g': [gauge('2Y 당일 변화(pp)', y2c1, '±0.10 이상이면 서프라이즈 급', st(y2c1 is not None and abs(y2c1) >= 0.10, missing=y2c1 is None)),
         gauge('2Y 5일 변화(pp)', y2c5, '누적 재가격 방향', 'na' if y2c5 is None else 'ok')]},
 'B': {'title': 'B 거시지표 (68건)', 'grammar': '채권 매개형 — 2Y 당일 상관 0.41(#022). 단 주식 자체 동학형 혼재(이질성 미해결).',
   'g': [gauge('2Y 당일 변화(pp)', y2c1, 'A칸과 계기 공유', 'na' if y2c1 is None else 'ok'),
         gauge('10Y 당일 변화(pp)', y10c1, '장기 재가격', 'na' if y10c1 is None else 'ok')]},
 'C': {'title': 'C 지정학·충격 (78건)', 'grammar': '에너지·통화형=달러 용량계(상관 0.69)·유가 보조. 보건형=VIX 경로만. 방향 비대칭: 고조 달러 0.57 / 완화 방출 0.33(#080).',
   'g': [gauge('달러 당일 %', dxc1, '급등=고조 용량', st(dxc1 is not None and abs(dxc1) >= 0.5, missing=dxc1 is None)),
         gauge('WTI 당일 %', wtc1, '보조 용량계', st(wtc1 is not None and abs(wtc1) >= 3, missing=wtc1 is None)),
         gauge('EPU 역사 백분위', epu_pct, '정책·확전 소음 수준', st(epu_pct is not None and epu_pct >= 90, missing=epu_pct is None))]},
 'D': {'title': 'D 신용·시스템 (5건)', 'grammar': '표본 5건 — 검증 영구 공백(이 기간에 신용위기 부재). MOVE는 관찰 계기일 뿐 판정 자산 없음.',
   'g': [gauge('MOVE', move_d and ys['move'][move_d], '채권 발작 온도계', st(move_d is not None and ys['move'][move_d] >= 120, missing=move_d is None)),
         gauge('MOVE 5일 변화', move_c5, '급등=시스템 경계', 'na' if move_c5 is None else 'ok')]},
 'E': {'title': 'E 빅테크 실적·섹터 (51건)', 'grammar': '직접형 — 주도주 절대등락 단조(#021). E.SECTOR_BLOW: SMH가 출력의 ~2배(#085).',
   'g': [gauge('smh_gap (SMH−NDX)', sg, '큰 음수=반도체발. 동시 판독 전용(전조 아님)', st(sg is not None and abs(sg) >= 2, cond_hot=sg is not None and sg <= -3, missing=sg is None))]},
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
               'move_curve_pct': mc_pct})
    save_csv(sc_path, sc, list(sc[-1].keys()))

msg = None
if event:
    hot = [f"{p['title']}: " + ', '.join(f"{g['name']}={g['val']}" for g in panels[k]['g'] if g['state'] in ('warn', 'hot'))
           for k, p in panels.items() if any(g['state'] in ('warn', 'hot') for g in p['g'])]
    msg = '\n'.join([f"[사건] {today} NDX {ret1:+.2f}% (3일 {cum3:+.2f}%)",
                     f"1개월 판정: {monthly['label']} — {monthly['why']}",
                     '경계 계기: ' + ('; '.join(hot) if hot else '전 계기 중립 (무전조형)'),
                     '다음: 세션 열어 루프 A 태깅 (원인 확정은 리서치로)'])
    with open(f'{D}/briefing_{today}.md', 'w', encoding='utf-8') as f:
        f.write(msg)
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
