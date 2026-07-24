# PRD — 에이전트 리셀 브로커 (Agentic Resell Broker)

> **Google Cloud × Solana AI Agentic Hackathon** · Track C: Multi-Agent Commerce
> 작성일 2026-07-24 · 마감 2026-08-03 23:59 KST · Demo Day 2026-08-21

---

## 1. 한 줄 요약 (Elevator Pitch)

> 일반 에이전트가 원하는 상품을 말하면, **쇼핑 에이전트**가 최저가를 소싱해 '에이전트 전용
> 결제 요청'으로 되팔고, 일반 에이전트의 지갑이 **사람 승인 없이 온체인 USDC로 자율 정산** →
> 우리가 실물을 매입해 지정 주소로 배송하는 **에이전트 리셀 브로커**.

한 문장 검증: *신뢰 없는 두 에이전트가 계좌·카드·사람 없이 즉시 정산한다 — 그래서 온체인이어야 한다.*

---

## 2. 문제 정의 (Problem)

- AI 에이전트가 상거래를 대신하는 시대가 왔지만, **에이전트가 스스로 결제할 금융 레일이 없다.**
  기존 카드·PG는 매 순간 사람의 승인을 전제로 설계됐고, 계좌 개설엔 신원·심사가 필요해
  에이전트는 계좌를 만들 수 없다.
- 대형 커머스(아마존·쿠팡)는 외부 에이전트의 프로그래매틱 구매를 **약관으로 차단**하고 있다
  (아마존은 Perplexity 무단구매 소송, 자체 Rufus만 허용). 롱테일·중소 머천트는 에이전트 커머스
  프로토콜에 편입될 길이 없다.
- 서로 모르는 두 에이전트 사이엔 **신뢰가 없다.** 선불하면 물건을 못 받을 위험, 후불하면 돈을
  못 받을 위험 — 중개자(카드사·에스크로 회사) 없이 즉시 정산할 방법이 필요하다.

**타깃 사용자**: 상품 구매를 위임받은 구매자 에이전트(및 그 배후 사용자) / 에이전트 채널로
판매하고 싶지만 자체 프로토콜이 없는 중소 머천트.

---

## 3. 솔루션 (Solution)

두 에이전트와 결제 레일로 구성된다.

- **① 일반 에이전트 (Buyer)** — 사용자를 대리. "이 상품 사줘 + 예산 + 배송지"를 A2A로 전달하고,
  결제 요청을 받으면 자기 지갑으로 **자율 서명**한다.
- **② 쇼핑 에이전트 (우리 = 헤드리스 머천트 브로커)** — 최저가를 소싱해 마진을 붙여 판매가를
  정하고, **에이전트-네이티브 결제 요청**을 발급한다. 온체인에서 결제를 검증한 뒤 Shopify에
  주문을 기록하고, 무대 뒤에서 실물을 매입해 배송한다.
- **결제 레일** — Solana devnet USDC. 결제 요청은 **Solana Pay transfer-request 프로토콜**
  (`payTo`, `amount`, `reference`). `reference` 태그로 클라이언트를 신뢰하지 않고 온체인에서
  주문-결제를 매칭·검증한다.

### 결제가 두 leg으로 분리되는 것이 핵심
| | 주체 | 레일 | 무대 | 심사 |
|---|---|---|---|---|
| **Leg 1** 일반 → 쇼핑 | 에이전트 지갑 자율 서명 | **온체인 USDC (Solana Pay)** | 라이브 | ★ 채점 핵심 |
| **Leg 2** 쇼핑 → 외부 머천트 | 우리가 재고 매입 | 기존 카드 레일 | 무대 뒤(운영) | 데모 제외 |

→ 채점되는 다리(Leg 1)는 100% 실제로 돌고, 터질 수 있는 다리(Leg 2)는 운영 리스크로 격리된다.

---

## 4. 아키텍처

![architecture]({{artifact:6059c7ae-1c1b-4da4-b10b-6dfb706b3ad2}})

- **위(심사 채점 영역)**: 두 에이전트 + Solana devnet + Shopify — 전부 라이브 동작.
- **아래(운영 영역)**: 외부 머천트 실물 이행 — 무대 뒤, 데모에서 비노출.

### 컴포넌트
| 컴포넌트 | 역할 | 스택 |
|---|---|---|
| 일반 에이전트 | 요청·자율결제 | Google ADK + Gemini, Solana 지갑 |
| 쇼핑 에이전트 | 소싱·가격설정·결제요청·검증 | Google ADK + Gemini, Solana 지갑, x402 옵션 |
| 결제/정산 | USDC 전송·검증 | Solana devnet, `@solana/web3.js` + `@solana/spl-token`, Solana Pay |
| 커머스 백엔드 | 카탈로그·주문 | Shopify Admin API (`orderCreate`, `orderMarkAsPaid`) |
| 배포 | 라이브 URL(가산점) | Google Cloud Run |

---

## 5. 메시지 흐름 (주문 1건 end-to-end)

![sequence]({{artifact:13bd4bc7-1d4f-4d92-8e5d-15e1d5272f9b}})

1. 일반 → 쇼핑: "이 상품 사줘" + 예산·수취주소 (A2A)
2. 쇼핑: Gemini로 최저가 소싱 · 마진 붙여 판매가 결정
3. 쇼핑 → 일반: 상품·판매가 + 결제요청 `{payTo, amount, reference}`
4. 일반: 지갑이 결제요청 확인 → **사람 승인 없이 서명**
5. 일반 → Solana: USDC(SPL) 전송 tx (reference 포함)
6. Solana: ~400ms 컨펌 · explorer에 tx 기록
7. 쇼핑 → Solana: reference로 결제 폴링·검증
8. Solana → 쇼핑: tx 확인됨 (금액·수취인 일치)
9. 쇼핑 → Shopify: `orderCreate` + `orderMarkAsPaid`
10. Shopify → 쇼핑: 주문 #확정
11. 쇼핑 → 일반: 주문확정 + tx explorer 링크 반환
12. 쇼핑 → 외부 머천트: 실물 매입·배송 (무대 뒤)

**5–8이 심사 핵심** — 목업 아닌 실제 온체인 결제를 증명하는 구간. explorer 링크가 증빙.

---

## 6. 데이터 계약 (Payment Request Schema)

```jsonc
// 쇼핑 에이전트 → 일반 에이전트 (Step 3)
{
  "productId":  "sku_1024",            // Shopify 상품 variant ID
  "title":      "무선 이어버드 Pro",
  "price":      { "amount": "25.00", "currency": "USDC" },
  "payTo":      "<merchant_wallet_pubkey>",   // 쇼핑 에이전트 수취 지갑
  "reference":  "<unique_pubkey>",     // 주문별 고유 태그 (온체인 검증 키)
  "orderRef":   "ord_20260803_0007",   // 내부 주문 상관관계 ID
  "network":    "solana-devnet",
  "expiresAt":  "2026-08-03T14:59:00Z"
}
```

```jsonc
// 일반 에이전트 → 쇼핑 에이전트 (Step 11 응답)
{
  "orderRef":   "ord_20260803_0007",
  "status":     "paid",
  "txSignature":"<base58_sig>",
  "explorer":   "https://explorer.solana.com/tx/<sig>?cluster=devnet",
  "shopifyOrderId": "gid://shopify/Order/..."
}
```

---

## 7. 결정 사항 (확정)

| # | 결정 | 값 | 비고 |
|---|---|---|---|
| 1 | 핵심 결제 레일 | **Solana Pay transfer-request 직접 구현** (지갑 자율 서명 + `reference` 온체인 검증) | x402/pay.sh는 심사 기준 ④ 정렬용 **옵션 레이어**로 여유 시 추가 |
| 2 | 네트워크 | **Devnet** | 무료 에어드랍 + 실제 explorer 링크. Localnet 개발 → Devnet 데모 |
| 3 | 정산 방식 | **직접 송금 MVP** | 일반 쇼핑몰 원리(결제=즉시 이행). 시간 되면 에스크로 확장 |
| 4 | 커머스 백엔드 | **Shopify Admin API** | 웹 체크아웃 페이지 사용 안 함(사람 승인=감점). 주문 장부 역할만 |

**핵심 제약**: 일반 에이전트에게 주는 것은 Shopify 웹 체크아웃 링크가 **아니라** 에이전트-네이티브
결제 요청이어야 한다. 지갑이 사람 클릭 없이 서명해야 자율결제로 인정된다.

---

## 8. 심사 기준 매핑 (역산)

| 심사 기준 | 우리 대응 | 증빙 |
|---|---|---|
| **① 프로덕트 소개서** (타깃/문제/수익모델/아키텍처) | 본 PRD | 문제·타깃·아키텍처 명시 |
| ├ 수익모델 | 아비트리지 마진(원가+마크업) + Shopify 제휴 커미션 | 상품별 원가/판매가 스프레드 |
| **② GitHub** (재현코드+README) | 모노레포 + README + 셋업 스크립트 | devnet에서 재현 가능 |
| **③ 3분 데모영상** (실제 결제 전과정) | Step 1–11 라이브, explorer tx 노출 | tx 서명 + explorer 링크 |
| **④ 혁신성·UX / AI활용도 / 인프라연동** | 헤드리스 머천트 엔드포인트 · Gemini 소싱 · USDC/Solana Pay(+x402 옵션) | 라이브 배포 URL(가산점) |

**목업 제외 게이트**: Demo Day 당일 devnet에서 실제 USDC tx가 발생·검증되어야 함.

---

## 9. 범위 (Scope)

**In (MVP · 데모 필수)**
- 두 에이전트 간 A2A 요청/응답
- Solana Pay 결제 요청 발급 + 지갑 자율 서명 + USDC devnet 전송
- `reference` 기반 온체인 결제 검증
- Shopify `orderCreate` + `orderMarkAsPaid`
- 사람용 데모 UI + tx explorer 링크 노출
- Cloud Run 배포

**Out (로드맵 / 무대 뒤)**
- Leg 2 외부 머천트(아마존·쿠팡) 실물 자동 매입 → 녹화 클립 or 목 어댑터
- 에스크로 스마트컨트랙트 (여유 시)
- x402/pay.sh 상호운용 레이어 (여유 시)
- Mainnet 전환

---

## 10. 리스크 & 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| 데모 당일 devnet 불안정 | 라이브 결제 실패 = 실격 | 사전 녹화 백업 + 리허설, RPC 다중화 |
| Shopify 웹 체크아웃 오용 | 사람 승인 결제로 감점 | 결제는 무조건 우리 결제요청+지갑서명, Shopify는 주문기록만 |
| 리테일 아비트리지(Leg 2) | 가격 역전·품절 | "가격 락 + 재고 확인 후 판매가 확정" (피치덱 방어) |
| Google Cloud 크레딧/계정 | 개발 지연 | 개인 Gmail로 $300 크레딧, Gemini는 무료 티어 API |

---

## 11. 일정 (마감 8/3 역산)

| 날짜 | 마일스톤 |
|---|---|
| ~7/25 | 결정 확정 + 계정·지갑·Shopify 개발스토어 셋업 |
| ~7/28 | **Leg 1 결제 왕복 devnet 동작** (핵심) |
| ~7/31 | A2A 협상 + Shopify 주문 연동 + Cloud Run 배포 |
| ~8/2 | 데모 영상 + 자체 심사 라운드 |
| 8/3 | 최종 제출 (23:59 KST) |
| 8/7 | 파이널리스트 ~10팀 발표 |
| 8/21 | Demo Day (Google Startup Campus, 오프라인) |

---

## 12. 성공 지표 (Definition of Done)

- [ ] devnet explorer에서 확인 가능한 실제 USDC 결제 tx 1건 이상
- [ ] 결제 순간 사람 클릭 0회 (자율결제 증명)
- [ ] Shopify에 `paid` 주문 자동 생성
- [ ] 라이브 배포 URL 접속 가능
- [ ] 3분 내 end-to-end 데모 재현
- [ ] "왜 온체인?" 한 문장 방어 성립
