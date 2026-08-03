#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NDX 감시 파이프라인 v0.1 (파일럿) — 우공이산 루프의 클라우드 무인판
매 거래일: 수집 -> 일별 패널 1행 -> 사건 검사 -> 경보 평가 -> status.json/브리핑 -> ntfy 푸시
설계 원칙: 기등록 규칙의 기계 적용만(문턱은 편람 그대로). 원천 실패는 결측 표기(추정 금지).
자산 계보: 연구 폴더 element_matrix v1.5 / holdout_panel_builder — findings_ledger #079~#087.
"""
import csv, json, os, io, urllib.request
from datetime import datetime, date, timedelta

D = 'docs/data'
FRED = os.environ.get('FRED_API_KEY', '')
NTFY = os.environ.get('NTFY_TOPIC', '')
UA = {'User-Agent': 'Mozilla/5.0 (watch-bot; research pipeline)'}

def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore')

def load_csv(path):
    with open(path, encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

def save_csv(path, rows, cols):
    # 열 보존형: 기존 행의 모든 키를 유지(잘림 방지), 결측은 공란
    allcols = list(cols)
    for r in rows:
        for k in r:
            if k and k not in allcols: allcols.append(k)
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=allcols, extrasaction='ignore', restval='')
        w.writeheader(); w.writerows(rows)

def upsert_series(path, datecol, valcol, new_pairs):
    rows = load_csv(path)
    have = {r[datecol] for r in rows}
    for d, v in new_pairs:
        if d not in have:
            rows.append({datecol: d, valcol: v})
    rows.sort(key=lambda r: r[datecol])
    save_csv(path, rows, [datecol, valcol])
    return rows

log = []
def note(msg):
    log.append(msg); print(msg)

# ---------- 1. 수집 (원천별 독립 — 실패해도 계속) ----------
# NDX (FRED 공식)
try:
    j = json.loads(get(f'https://api.stlouisfed.org/fred/series/observations?series_id=NASDAQ100&api_key={FRED}&file_type=json&observation_start=2026-05-01'))
    pairs = [(o['date'], round(float(o['value']), 2)) for o in j['observations'] if o['value'] != '.']
    # FRED = 공식 종가 원천 — 겹치는 날짜는 덮어씀(잠정치·수정치 정정)
    ndx_rows = load_csv(f'{D}/ndx_daily.csv')
    bymap = {r['Date']: r for r in ndx_rows}
    for d_, v_ in pairs:
        if d_ in bymap: bymap[d_]['Close'] = v_
        else: ndx_rows.append({'Date': d_, 'Close': v_}); bymap[d_] = ndx_rows[-1]
    ndx_rows.sort(key=lambda r: r['Date'])
    save_csv(f'{D}/ndx_daily.csv', ndx_rows, ['Date', 'Close'])
    note(f"NDX(FRED) ok ~{pairs[-1][0]}")
except Exception as e:
    ndx_rows = load_csv(f'{D}/ndx_daily.csv'); note(f"NDX 실패: {e}")

# VIX (Cboe 공식)
try:
    txt = get('https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv')
    pairs = []
    for r in csv.DictReader(io.StringIO(txt)):
        d = r.get('DATE') or list(r.values())[0]
        try:
            m, dd, y = d.split('/'); iso = f'{y}-{int(m):02d}-{int(dd):02d}'
            pairs.append((iso, float(r.get('CLOSE') or list(r.values())[-1])))
        except Exception: pass
    vix_rows = upsert_series(f'{D}/vix.csv', 'Date', 'Close', pairs[-40:])
    note(f"VIX(Cboe) ok ~{pairs[-1][0]}")
except Exception as e:
    vix_rows = load_csv(f'{D}/vix.csv'); note(f"VIX 실패: {e}")

# SMH (야후 -> 실패 시 결측 허용)
try:
    j = json.loads(get('https://query1.finance.yahoo.com/v8/finance/chart/SMH?range=1mo&interval=1d'))
    res = j['chart']['result'][0]
    pairs = [(datetime.utcfromtimestamp(t).strftime('%Y-%m-%d'), round(c, 2))
             for t, c in zip(res['timestamp'], res['indicators']['quote'][0]['close']) if c]
    smh_rows = upsert_series(f'{D}/smh_daily.csv', 'Date', 'Close', pairs)
    note(f"SMH(야후) ok ~{pairs[-1][0]}")
except Exception as e:
    smh_rows = load_csv(f'{D}/smh_daily.csv'); note(f"SMH 실패(결측 허용): {e}")

# COT (CFTC 공식 — 주간)
try:
    txt = get("https://publicreporting.cftc.gov/resource/gpe5-46if.json?cftc_contract_market_code=209742&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=4")
    js = json.loads(txt)
    lev = [(e['report_date_as_yyyy_mm_dd'][:10], int(e['lev_money_positions_short']) - int(e['lev_money_positions_long'])) for e in js]
    rows = load_csv(f'{D}/cot_tff_nq_lev.csv'); have = {r['report_date'] for r in rows}
    for e in js:
        d = e['report_date_as_yyyy_mm_dd'][:10]
        if d not in have:
            rows.append({'report_date': d, 'lev_long': e['lev_money_positions_long'], 'lev_short': e['lev_money_positions_short']})
    rows.sort(key=lambda r: r['report_date'])
    save_csv(f'{D}/cot_tff_nq_lev.csv', rows, ['report_date', 'lev_long', 'lev_short'])
    da = load_csv(f'{D}/cot_tff_nq_dealer_am.csv'); have2 = {r['report_date'] for r in da}
    for e in js:
        d = e['report_date_as_yyyy_mm_dd'][:10]
        if d not in have2:
            da.append({'report_date': d, 'dealer_long': e['dealer_positions_long_all'], 'dealer_short': e['dealer_positions_short_all'],
                       'am_long': e['asset_mgr_positions_long'], 'am_short': e['asset_mgr_positions_short']})
    da.sort(key=lambda r: r['report_date'])
    save_csv(f'{D}/cot_tff_nq_dealer_am.csv', da, ['report_date', 'dealer_long', 'dealer_short', 'am_long', 'am_short'])
    cot_rows = rows; note(f"COT ok ~{rows[-1]['report_date']}")
except Exception as e:
    cot_rows = load_csv(f'{D}/cot_tff_nq_lev.csv'); da = load_csv(f'{D}/cot_tff_nq_dealer_am.csv'); note(f"COT 실패: {e}")

# FINRA 메가캡 SVR (전 거래일)
MEGA = {'AAPL', 'MSFT', 'NVDA', 'AMZN', 'TSLA', 'GOOGL', 'META'}
try:
    svr_rows = load_csv(f'{D}/svr_megacap.csv'); have = {r['date'] for r in svr_rows}
    added = 0
    dd = date.today()
    for back in range(1, 15):
        t = dd - timedelta(days=back)
        if t.weekday() >= 5: continue
        iso = t.isoformat(); ymd = iso.replace('-', '')
        if iso in have: continue
        try:
            txt = get(f'https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt')
            s = tot = 0.0
            for line in txt.splitlines():
                p = line.split('|')
                if len(p) >= 5 and p[1] in MEGA:
                    try: s += float(p[2]); tot += float(p[4])
                    except Exception: pass
            if tot > 0:
                svr_rows.append({'date': iso, 'svr': round(s / tot, 4)}); added += 1
        except Exception: pass
    svr_rows.sort(key=lambda r: r['date'])
    if added: save_csv(f'{D}/svr_megacap.csv', svr_rows, ['date', 'svr'])
    note(f"FINRA SVR +{added} ~{svr_rows[-1]['date'] if svr_rows else '없음'}")
except Exception as e:
    svr_rows = load_csv(f'{D}/svr_megacap.csv'); note(f"FINRA 실패: {e}")

# Equity P/C (Cboe CDN — 전 거래일)
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

# ---------- 2. 패널·규칙 (기등록 문턱 그대로 — findings_ledger 계보) ----------
ndx = {r['Date']: float(r['Close']) for r in ndx_rows}
dates = sorted(ndx); idx = {d: i for i, d in enumerate(dates)}
vix = {r['Date']: float(r['Close']) for r in vix_rows}
smh = {r['Date']: float(r['Close']) for r in smh_rows}
svr = {r['date']: float(r['svr']) for r in svr_rows}
pc = {r['date']: float(r['equity_pc']) for r in pc_rows}
cot = sorted((r['report_date'], int(r['lev_short']) - int(r['lev_long'])) for r in cot_rows)
dealer = sorted((r['report_date'], int(r['dealer_long']) - int(r['dealer_short'])) for r in da)

today = dates[-1]
i = idx[today]
ret1 = 100 * (ndx[today] / ndx[dates[i-1]] - 1)
cum3 = 100 * (ndx[today] / ndx[dates[i-3]] - 1) if i >= 3 else 0.0
event = abs(ret1) >= 2 or abs(cum3) >= 5
v = vix.get(today)
vk = sorted(vix); vpos = {d: k for k, d in enumerate(vk)}

def vix_1y_pct(d):  # CAND 3호 (#083)
    k = vpos.get(d)
    if k is None or k < 252: return None
    win = [vix[vk[j]] for j in range(k-251, k+1)]
    return round(100 * sum(1 for x in win if x <= vix[d]) / 252)

def vix_rel_jump_pct(d):  # 동시 강도 (#083 A2)
    k = vpos.get(d)
    if k is None or k < 1: return None
    rels = [vix[vk[j]] / vix[vk[j-1]] - 1 for j in range(1, len(vk))]
    x = vix[d] / vix[vk[k-1]] - 1
    return round(100 * sum(1 for r in rels if r <= x) / len(rels), 1)

def smh_gap(d):  # v1.5 (#085-#086) — 동시 판독 전용
    k = idx.get(d)
    if k is None or k < 1: return None
    p = dates[k-1]
    if d in smh and p in smh:
        return round(100 * (smh[d] / smh[p] - 1) - 100 * (ndx[d] / ndx[p] - 1), 2)
    return None

def nsp(d):  # #045 규약
    kk = None
    for j in range(len(cot) - 1, -1, -1):
        if cot[j][0] < d: kk = j; break
    if kk is None or kk < 51: return None
    win = [c[1] for c in cot[kk-51:kk+1]]
    return round(100 * sum(1 for x in win if x <= cot[kk][1]) / len(win))

def svr_rel(d):  # #049 표준 산식 (직전 20일 제외창)
    sd = sorted(svr); sp = {x: k for k, x in enumerate(sd)}
    k = sp.get(d)
    if k is None or k < 20: return None
    win = [svr[sd[j]] for j in range(k-20, k)]
    return round(100 * sum(1 for x in win if x <= svr[d]) / 20)

dealer_net = dealer[-1][1] if dealer else None
sr = svr_rel(today); p_c = pc.get(today); np_ = nsp(today)
alarms = {
    'vix28_regime': bool(v and v >= 28),
    'vix35_confirm': bool(v and v >= 35),           # CAND 2호: 발동=10일 내 사건 확정
    'vix_1y_pctile': vix_1y_pct(today),              # CAND 3호: >=90 상대 공포 국면
    'vix_rel_jump_pctile': vix_rel_jump_pct(today),  # 동시 강도
    'smh_gap': smh_gap(today),                       # 동시 판독 (전조 아님)
    'nsp': np_, 'svr_rel': sr, 'equity_pc': p_c, 'dealer_net': dealer_net,
    'rule_momentum': bool(ret1 > 0 and sr is not None and sr <= 20 and p_c is not None and p_c <= 0.55),
    'rule_s1_squeeze': bool(ret1 > 0 and sr is not None and sr >= 80 and (np_ or 0) >= 80),
}

# ---------- 3. 상태 파일 + 브리핑 + 푸시 ----------
status = {
    'updated_utc': datetime.utcnow().isoformat() + 'Z', 'date': today,
    'ndx': ndx[today], 'ret1_pct': round(ret1, 2), 'cum3_pct': round(cum3, 2),
    'event': event, 'vix': v, 'alarms': alarms, 'collect_log': log,
    'footnotes': {
        'vix28': '#070: 신호의 몸통은 국면 자체 — 하락과 함께 국면 갓 진입한 첫날은 68%. 하락 첫날 즉시 매수 금지.',
        'vix35': '#072: 발동 시 10일 내 ±2% 사건 사실상 확정(11/11). 재현율 낮음 — 침묵은 무주장.',
        'vix_1y': '#083: >=90 = 상대 공포 국면(사건률 72~100%). 무전조 캄 크래시는 사각.',
        'smh_gap': '#085: 동시 판독 전용(전조 아님). 저VIX 체제에서 사건 유형 감별 최강(6.3배).',
        'rules': '#070: G 판정식은 사후 원인 분류 전용 — 예측 신호로 사용 금지.',
    }
}
with open(f'{D}/status.json', 'w', encoding='utf-8') as f:
    json.dump(status, f, ensure_ascii=False, indent=1)

# 일별 스코어카드 누적
sc_path = f'{D}/daily_scorecard.csv'
sc = load_csv(sc_path) if os.path.exists(sc_path) else []
if not any(r['date'] == today for r in sc):
    sc.append({'date': today, 'ret1': round(ret1, 2), 'cum3': round(cum3, 2), 'event': int(event),
               'vix': v, 'vix_1y_pct': alarms['vix_1y_pctile'], 'smh_gap': alarms['smh_gap'],
               'nsp': np_, 'svr_rel': sr, 'pc': p_c, 'dealer_net': dealer_net})
    save_csv(sc_path, sc, list(sc[-1].keys()))

msg = None
if event:
    kind = '급락' if ret1 < -0 else '급등'
    lines = [f"[사건] {today} NDX {ret1:+.2f}% (3일 {cum3:+.2f}%)",
             f"강도: VIX상대급변 {alarms['vix_rel_jump_pctile']}백분위 | VIX {v}",
             f"동시판독: smh_gap {alarms['smh_gap']} (음수 크면 반도체발) | 수급 SVR {sr}/P/C {p_c}/NSP {np_}",
             f"판정식: 모멘텀 {alarms['rule_momentum']} S1 {alarms['rule_s1_squeeze']} (사후 분류용)",
             "다음: 세션 열어 루프 A 태깅(원인 확정은 리서치로 — 블라인드 35% 한계).",]
    if ret1 < 0 and v and v >= 28:
        lines.append("VER: VIX>=28 — 1개월 반등 84~94% (각주: 국면 첫날이면 68%)")
    msg = '\n'.join(lines)
    with open(f'{D}/briefing_{today}.md', 'w', encoding='utf-8') as f:
        f.write(msg)
elif alarms['vix35_confirm']:
    msg = f"[확진] {today} VIX {v} >= 35 — 10일 내 ±2% 사건 사실상 확정(#072)"
elif alarms['vix_1y_pctile'] and alarms['vix_1y_pctile'] >= 90:
    msg = f"[국면] {today} VIX 1년 백분위 {alarms['vix_1y_pctile']} — 상대 공포 국면(#083)"

if msg and NTFY:
    try:
        req = urllib.request.Request(f'https://ntfy.sh/{NTFY}', data=msg.encode('utf-8'),
                                     headers={'Title': 'NDX watch', 'Priority': 'high' if event else 'default'})
        urllib.request.urlopen(req, timeout=15)
        note('ntfy 푸시 발송')
    except Exception as e:
        note(f'ntfy 실패: {e}')

note(f"완료: {today} ret {ret1:+.2f}% event={event}")
