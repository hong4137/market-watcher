# stage46: 빅테크 실적일 역사 수집 (연구 H1용)
import csv, os
os.makedirs('docs/data/ext45', exist_ok=True)
import yfinance as yf
rows=[]
for t in ('MSFT','AAPL','NVDA','GOOGL','AMZN','META','TSLA','AVGO'):
    try:
        ed=yf.Ticker(t).get_earnings_dates(limit=80)
        for ts in ed.index:
            d=str(ts)[:10]
            if d>='2015-01-01': rows.append([t,d])
        print(t, len(ed))
    except Exception as e: print(t,'실패',e)
rows=sorted(set(map(tuple,rows)))
_p='docs/data/ext45/earnings_mag7.csv'
_old=(sum(1 for _ in open(_p))-1) if os.path.exists(_p) else 0
if len(rows) < 0.9*_old:
    print(f'응답 {len(rows)}행 < 기존 {_old}행의 90% — 기존 파일 유지(2026-11-11 보호)')
else:
    with open(_p,'w',newline='') as f:
        w=csv.writer(f); w.writerow(['ticker','date']); w.writerows(rows)
    print('저장', len(rows))
