# NDX Watch — 무인 감시 대시보드 (파일럿 v0.1)

컴퓨터를 꺼놔도 GitHub 서버가 매 거래일 마감 후(한국시간 19:30) 자동으로:
수집 → 계기판 갱신 → 사건(±2%/3일 ±5%) 검사 → 사건이면 폰으로 푸시 알림.

- 실행: GitHub Actions (공개 리포 무료·무제한)
- 화면: GitHub Pages (정적 웹 — 폰·PC 어디서나 열람)
- 알림: ntfy.sh (계정 불필요 무료 푸시)
- 데이터: FRED(NDX)·Cboe(VIX, P/C)·CFTC(COT)·FINRA(SVR)·야후(SMH, 폴백)

## 설치 (1회, 약 15분)

### 1. GitHub 리포 만들기
1. github.com 가입(이미 있으면 생략) → 우상단 **+** → **New repository**
2. 이름 예: `ndx-watch`, **Public** 선택(Public이어야 Actions 무료 무제한), **Create**

### 2. 파일 업로드
1. 리포 화면에서 **uploading an existing file** 클릭
2. 이 `dashboard/` 폴더 **안의 내용물 전부**를 드래그 (`.github` 폴더 포함 — 탐색기에서 숨김 표시일 수 있음)
   - 웹 업로드가 `.github` 폴더를 못 받으면: 리포에서 **Add file → Create new file** → 파일명에 `.github/workflows/daily.yml` 입력 → 내용 붙여넣기
3. **Commit changes**

### 3. FRED 키 등록 (⚠ 코드에 직접 쓰지 말 것 — 암호화 금고에만)
1. 리포 **Settings → Secrets and variables → Actions**
2. **New repository secret** → Name: `FRED_API_KEY`, Secret: 발급받은 키 → **Add secret**

### 4. 푸시 알림 (선택이지만 강력 추천)
1. 폰에 **ntfy** 앱 설치(App Store/Play 무료)
2. 앱에서 **+ Subscribe to topic** → 아무도 못 맞힐 이름 입력 (예: `ndx-watch-jae-7x9q2`)
3. 리포 **Settings → Secrets and variables → Actions → Variables 탭** → **New repository variable**
   → Name: `NTFY_TOPIC`, Value: 위에서 정한 토픽 이름

### 5. 첫 실행 + 화면 켜기
1. 리포 **Actions 탭** → 좌측 `daily-watch` → **Run workflow** (수동 1회 실행) → 초록 체크 확인
2. **Settings → Pages** → Branch: `main`, 폴더: `/docs` → **Save**
3. 1~2분 후 `https://<아이디>.github.io/ndx-watch/` 접속 — 이게 대시보드 주소 (폰 홈화면에 추가 권장)

## 이후 운영
- 매일 아무것도 안 해도 됨. 화~토 한국 19:30경 자동 실행.
- 사건 발생 → 폰 푸시 + 대시보드 브리핑 카드. **원인 태깅·진짜/가짜 판정은 세션을 열어 루프 A로** (기계는 수치 명세까지만 — 블라인드 역추적 35% 한계는 실측된 사실).
- 주간 원천(COT)은 금요일 발표분이 다음 실행에 반영.
- 실패한 원천은 그날 결측 표기(추정 금지 원칙) — 대시보드 하단 collect_log에서 확인 가능.

## 한계 (정직 고지)
- FINRA·Cboe 당일 파일 게시 지연 시 해당 계기는 하루 늦음.
- 야후(SMH)는 GitHub 공유 IP에서 간헐 차단(429) 가능 — smh_gap만 결측되고 나머지는 정상.
- 무전조 캄 크래시(저VIX 사각)는 이 대시보드도 못 잡음 — #084에 명기된 미해결 사각.

## 자산 계보
문턱·산식은 연구 폴더의 확정 자산을 그대로 이식: findings_ledger #001(VER)·#049·#066·#070·#072·#082·#083·#085, category_calculus_handbook. 이 리포에서 문턱을 수정하지 않는다(수정은 연구 폴더의 상정→승인 절차로만).
