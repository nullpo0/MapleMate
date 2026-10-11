# 캐릭터별 상담 state 읽기와 갱신

MCP topic은 `state`다. 자연어는 클라이언트 LLM이 정리하고, 저장은 MCP 도구 호출로 수행한다. 대화를 읽었다는 사실만으로 로컬 저장이 완료되는 것은 아니다.

## 네 종류의 정보를 구분하기

| 정보 | 저장 경로 | 출처와 시각 |
| --- | --- | --- |
| 사용자 목표·제약·경험 | `update_player_context` | `user_input`, 항목별 `updated_at` |
| 화면의 닉네임·메소·툴팁 | `record_screen_observation` | `llm_vision`, 캡처 ID·촬영 시각; 서버가 판독 내용의 정확성을 검증한 것은 아님 |
| 공식 캐릭터·유니온 성장 데이터 | 성공한 API 조회 시 자동 저장 | `nexon_open_api`, `data_date`·`requested_date`·`fetched_at` |
| 성장 단계·이해도 판단 | `record_growth_assessment` | `llm_assessment`, 근거·불확실성·판단 시각·기준 revision |

`get_player_state(character_name=...)`는 이미 저장한 정보만 읽는다. 새 캡처나 API 요청을 발생시키지 않는다. `user_context`는 값, `context_facts`는 같은 값의 출처와 갱신 시각이다. `legacy_context`와 null 시각은 기존 기록의 출처/시각이 확인되지 않은 것이다. 오래된 기록을 방금 사용자가 다시 말한 것으로 바꾸지 않는다.

`api_observations`는 기본적으로 endpoint·요청 조건·기준일·조회 시각만 제공한다. 원문이 필요한 경우 `include_api_data=True`로 읽는다. 실제 판단에는 해당 목표에 필요한 관찰만 사용하고, 역사 날짜·스킬 등급·프리셋의 다른 조회를 현재 세팅으로 합치지 않는다. 저장된 정보가 부족하거나 오래됐으면 필요한 API만 새로 조회한다. 전체 계정 이력·랭킹·공지 등은 상담 state에 자동 누적하지 않는다.

## 사용자가 직접 말한 항목 저장하기

최근 state를 읽고 새로 확인한 항목만 `patch`에 넣는다. `expected_revision`에는 읽은 state의 `revision`을 사용한다. 생략한 항목은 유지되고, 명시한 `null`은 초기화된다. 0 메소/0분은 확인된 0이며 미확인값과 다르다. 오류가 나면 최신 state를 다시 읽고 사용자 발언과 대조한 후 필요한 변경만 다시 적용한다.

| patch 항목 | 의미 |
| --- | --- |
| `goal`, `play_style` | 목표와 플레이 취향의 짧은 설명; 기존 개별 인자도 지원 |
| `target_content`, `target_difficulty`, `target_party_size`, `target_date` | 실제 목표 콘텐츠·난이도·인원·목표일(YYYY-MM-DD) |
| `budget_meso`, `reserve_meso` | 이번 투자에 쓸 수 있다고 말한 금액, 남겨두려는 금액. budget_meso에서 reserve_meso를 다시 차감하지 않음 |
| `spending_preference` | `no_paid_spending`, `consider_paid_spending`, `undecided` |
| `daily_play_minutes`, `weekly_play_minutes` | 하루/주 플레이 가능 시간(분); 한쪽에서 다른 쪽을 임의 산출하지 않음 |
| `weekly_hunting_minutes`, `weekly_boss_minutes` | 주당 실제 사용할 사냥/보스 시간(분) |
| `risk_preference` | 사용자가 말한 강화 손실·파괴 위험에 대한 성향 |
| `system_familiarity`, `liberation_progress` | 사용자의 시스템 이해에 대한 자기 설명, 해방 진행 상황 |
| `boss_experience` | 사용자가 말한 보스·난이도·인원별 경험 기록 |

보스 기록에는 `boss_name`, `difficulty`, `party_size`, `status`를 넣는다. status는 `not_attempted`·`practicing`·`cleared`·`stable` 중 발언에 맞는 값이다. `clear_time_minutes`·`notes`는 실제 확인한 경우에만 추가한다. 같은 보스·난이도·인원의 기록은 병합되고 다른 기록은 유지된다. 선택 항목을 생략하면 이전 값이 유지되므로 더 이상 유효하지 않은 값은 명시한 null로 초기화한다. `boss_experience=null`은 전체 보스 경험 기록을 초기화한다.

예: “이번에는 10억까지 쓰고 하루 30분 해”라고 말했을 때 저장할 내용:

```json
{
  "character_name": "확인한 캐릭터",
  "patch": {"budget_meso": 1000000000, "daily_play_minutes": 30},
  "expected_revision": 7
}
```

7은 예시이며 실제로 읽은 revision으로 바꾼다. 목표나 전체 잔액, 주간 시간까지 추정해 채우지 않는다. 상충하는 시간·예산 발언이 있으면 필요한 확인을 하고, LLM이 제안한 목표·예산을 사용자 확정값으로 저장하지 않는다. 관찰된 메소·장비는 이 patch에 넣지 않는다.

목표가 바뀌면 이전 난이도·인원·기한·예산이 새 목표에도 유효한지 확인한다. 값이 존재한다는 이유만으로 새 목표의 조건으로 재사용하지 않는다.

## 성장 판단 저장과 재검토

개인 성장 추천을 위해 진단한 경우 [progression.md](progression.md)의 근거를 갖추고 `record_growth_assessment`로 잠정 판단을 저장할 수 있다. 단순 용어 설명·수치 계산에는 분류나 저장을 추가하지 않는다.

필요한 사용자 입력·화면 관찰·API 조회를 마친 뒤 state를 읽는다. 판단에는 `stage`(beginner/intermediate/advanced/unknown), `system_understanding`(learning/mixed/experienced/unknown), `goal`, `rationale`, `bottlenecks`, `uncertainties`, `confidence`(low/medium/high)를 넣고 읽은 `revision`을 `based_on_revision`으로 전달한다. 근거에는 읽은 항목·기준일·조건을 연결하고 미확인값을 숨기지 않는다. 자기 설명인 system_familiarity와 LLM의 system_understanding은 별개다.

사용자 항목·화면 관찰·API 내용이 바뀌면 저장된 판단에 `needs_review=true`가 붙는다. 같은 API 내용을 다시 조회한 것만으로는 무효화하지 않는다. 표시가 false여도 판단의 `assessed_at`/나이, 패치·기간제 만료·실제 목표 변경과 관련 데이터의 시각을 확인한다. 서버는 게임의 패치·만료를 스스로 추적하지 않으며, 단계 자체를 자동 판정하지 않는다.

오래된 대화의 판단 저장은 revision 불일치로 거절된다. 이때 최신 사실을 읽고 추천을 다시 검토한다. 오류를 피하려고 revision만 새 숫자로 바꾸지 않는다. 저장 도구가 성공한 뒤에만 사용자에게 저장됐다고 말한다.
