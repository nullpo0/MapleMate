# MapleMate

Codex와 Claude Desktop에 한국 메이플스토리 정보와 계산 도구를 연결하는 Windows 앱입니다. 공식 Nexon API, 요청 시 게임 화면 캡처, 캐릭터 기록, 최종뎀 효율 계산을 **Maple-mcp**로 제공하고, **maplemate skill**이 질문에 맞는 정보 수집과 답변 지침을 제공합니다. 질문 해석과 이미지 판독은 연결한 AI가 담당합니다.

## 시작하기

**Windows 10/11 x64**에서 사용합니다. **Python을 설치할 필요가 없습니다.** Codex 또는 Claude Desktop과 Nexon API Key는 별도로 준비하세요.

1. GitHub Releases에서 `MapleMate-1.0.0-windows-x64.zip`을 내려받아 **폴더 전체를 압축 해제**합니다.
2. `MapleMate.exe`를 실행하고 Nexon API Key를 저장합니다.
3. 설정에서 **Codex 연결** 또는 **Claude Desktop 연결**을 누릅니다.
4. 연결한 AI 앱을 재시작하고 질문합니다.

```text
Codex: $maplemate 지금 접속 중인 캐릭터 정보 정리해줘
Claude Desktop: MapleMate로 이 장비를 교체하면 최종뎀 얼마나 오르는지 알려줘
```

이벤트·퀘스트·직업·용어 같은 일반 질문도 할 수 있습니다. 성장 추천에는 캐릭터 수준과 목표·예산·플레이 시간을 반영합니다. API에 없는 최신 정보는 AI의 검색 기능으로 보완합니다.

## 앱 사용

- 실행하면 설정 창과 Windows 알림이 나타납니다. 창을 닫아도 트레이에서 실행하며, 트레이 메뉴의 **종료**로 끝냅니다.
- **연결**에서 API 키와 AI 연결 상태를, **캐릭터**에서 저장된 목표·예산·관찰 시각을 확인합니다. 캐릭터 목록은 API로 조회합니다.
- 캐릭터별 **사용자 설정** 또는 **설정 + 관찰 기록**을 초기화할 수 있습니다. API 키·AI 연결·다른 캐릭터의 기록은 유지합니다.
- 이미지를 AI에 직접 첨부하거나 현재 게임 화면을 읽어 달라고 요청할 수 있습니다. 캡처는 요청할 때만 수행하고, 큰 이미지는 자동으로 압축합니다.

`MapleMate-mcp.exe`는 AI가 실행하는 MCP 서버입니다. 사용자가 직접 열 필요는 없지만 `MapleMate.exe`, `_internal`과 같은 폴더에 두어야 합니다. 폴더를 옮겼다면 AI 연결 버튼을 다시 누르세요.

## 제공하는 정보와 계산

- 공식 KMS **55개 GET API**: 캐릭터·장비·스킬·유니온·길드·랭킹·공지·이벤트·강화 이력·콘텐츠 진행 상태 등. 질문에 필요한 항목만 조회하며 API 키 권한과 공식 조회 제한을 따릅니다.
- 캐릭터별 목표·예산·플레이 시간·보스 경험·화면 관찰·API 조회 기록. 출처와 기준 시각을 구분하며 마지막 관찰을 실시간 상태로 표현하지 않습니다.
- 장비 교체 전후 딜 증가율을 최종뎀으로 환산하고, 최종뎀 1%당 메소 비용·강화 기대값·스킬별 가중 효율을 계산합니다. 부족한 입력은 추가로 확인합니다.
- Codex에는 **maplemate skill 하나**를 설치합니다. Claude Desktop은 동일한 지침을 MCP guide와 prompt로 받습니다.

계산은 스탯·공/마·데미지·최종뎀·방무·크리티컬·속성의 전후 비율을 사용합니다. **환산주스탯 사이트 전체 모델을 재현하거나 동일 결과를 보장하지는 않습니다.** 직업별 스킬·쿨감·실전 가동률 등의 영향을 별도로 확인해야 합니다. 상세 공식과 입력 규칙은 maplemate skill의 damage 지침에 있습니다.

## 데이터와 개인정보

기본 저장 위치는 `%LOCALAPPDATA%\MapleMate`입니다. 기존 `.maplemcp`·`.maplemate` 또는 기존 AI 연결에 지정된 데이터 폴더를 이어받습니다. 명시적 `--data-dir`와 `MAPLEMATE_DATA_DIR`도 지원합니다.

API 키는 Windows DPAPI로 암호화합니다. 요청한 게임 이미지와 조회 정보는 연결한 AI 서비스에 전달하지만, API 키와 로컬 연결 인증정보는 전달하지 않습니다. 게임 이미지 픽셀은 디스크에 저장하지 않습니다. 게임 입력 자동화·메모리 조회·상시 화면 감시는 하지 않습니다.

게임이 최소화되어 있거나 여러 게임 창이 열려 있으면 캡처가 실패할 수 있습니다. 이미지 판독을 지원하는 AI 모델이 필요합니다. 초기화는 로컬 기록에만 적용되며 AI 앱의 대화 내용은 별도로 관리합니다.

## 개발과 배포

소스 실행·테스트·구조·릴리스 절차는 소스 저장소의 `docs/DEVELOPMENT.md`, 이번 릴리스의 변경 사항은 [CHANGELOG](CHANGELOG.md)를 참고하세요. GitHub Actions는 검증된 Windows ZIP과 체크섬을 만들고 `v1.0.0` 태그에 대한 **초안 릴리스**를 생성합니다.

공식 자료: [Nexon Open API](https://openapi.nexon.com/ko/game/maplestory/), [Windows 화면 캡처](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture), [로컬 MCP 연결](https://modelcontextprotocol.io/docs/develop/connect-local-servers).
