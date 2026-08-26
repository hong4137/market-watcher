# stage45: 뉴스·국제관계 텍스트 지수 수집 (연구용 — 대시보드 무관)
import requests, csv, io, os, re
OUT='docs/data/ext45'; os.makedirs(OUT, exist_ok=True)
H={'User-Agent':'Mozilla/5.0 (research; market-watcher)'}
def log(m): print(m, flush=True)
def save(name, rows, cols):
    with open(f'{OUT}/{name}.csv','w',newline='') as f:
        w=csv.writer(f); w.writerow(cols); w.writerows(rows)
    log(f'{name}: {len(rows)}행 저장 ({rows[0][0]}~{rows[-1][0]})' if rows else f'{name}: 0행')
# 1) FRED fredgraph (키 불요)
for sid,name in (('WLEMUINDXD','wlemu_daily'),('INFECTDISEMVTRACKD','infectemv_daily')):
    try:
        r=requests.get(f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}',headers=H,timeout=60)
        rows=[]
        for ln in csv.reader(io.StringIO(r.text)):
            if len(ln)==2 and re.match(r'\d{4}-\d{2}-\d{2}',ln[0]):
                try: rows.append((ln[0],float(ln[1])))
                except: pass
        save(name,rows,['Date','Close'])
    except Exception as e: log(f'{name} 실패: {e}')
def xlsx_all(url,prefix):
    try:
        r=requests.get(url,headers=H,timeout=120); r.raise_for_status()
        import openpyxl
        wb=openpyxl.load_workbook(io.BytesIO(r.content),data_only=True,read_only=True)
        for ws in wb.worksheets:
            rows=list(ws.iter_rows(values_only=True))
            if not rows: continue
            hdr=[str(x) for x in rows[0]]
            out=[]
            for rr in rows[1:]:
                if rr and rr[0] is not None: out.append([str(x) if x is not None else '' for x in rr])
            nm=f"{prefix}_{re.sub('[^A-Za-z0-9]','',ws.title)[:20]}"
            with open(f'{OUT}/{nm}.csv','w',newline='') as f:
                w=csv.writer(f); w.writerow(hdr); w.writerows(out)
            log(f'{nm}: {len(out)}행 (cols={hdr[:5]})')
    except Exception as e: log(f'{prefix} 실패: {url} {e}')
# 2) SF연준 일간 뉴스 감성 — 페이지에서 xlsx 링크 탐색 + 알려진 경로 폴백
sf_urls=['https://www.frbsf.org/wp-content/uploads/news_sentiment_data.xlsx',
         'https://www.frbsf.org/wp-content/uploads/sites/4/news_sentiment_data.xlsx']
try:
    p=requests.get('https://www.frbsf.org/research-and-insights/data-and-indicators/daily-news-sentiment-index/',headers=H,timeout=60)
    sf_urls=[m for m in re.findall(r'href="([^"]+\.xlsx)"',p.text)]+sf_urls
except Exception as e: log(f'SF 페이지 실패: {e}')
for u in sf_urls:
    if not u.startswith('http'): u='https://www.frbsf.org'+u
    try:
        xlsx_all(u,'sf_sentiment'); break
    except Exception: continue
# 3) 트위터 경제 불확실성 TEU
for u in ['https://www.policyuncertainty.com/media/Twitter_Economic_Uncertainty.xlsx',
          'https://www.policyuncertainty.com/media/Updated_Twitter_Uncertainty.xlsx']:
    try:
        xlsx_all(u,'teu'); break
    except Exception: continue
# 4) 무역정책 불확실성 TPU (Iacoviello)
for u in ['https://www.matteoiacoviello.com/tpu_files/tpu_web_latest.xlsx',
          'https://www.matteoiacoviello.com/tpu_files/TPU_WEB_LATEST.xlsx']:
    try:
        xlsx_all(u,'tpu'); break
    except Exception: continue
# 5) BBD 미국 일간 EPU 원본 재수집(대조용) + 카테고리 데이터 페이지 탐색
try:
    p=requests.get('https://www.policyuncertainty.com/categorical_epu.html',headers=H,timeout=60)
    links=re.findall(r'href="([^"]+\.xlsx)"',p.text)
    log(f'카테고리 EPU 링크 후보: {links[:5]}')
    if links:
        u=links[0]
        if not u.startswith('http'): u='https://www.policyuncertainty.com/'+u.lstrip('/')
        xlsx_all(u,'epu_cat')
except Exception as e: log(f'카테고리 EPU 실패: {e}')
log('완료')
