# MapleMate

Codex와 Claude Desktop에 메이플스토리 정보와 계산 도구를 연결하는 Windows 앱입니다. 공식 Nexon API, 요청 시 게임 화면 캡처, 캐릭터 기록, 최종뎀 효율 계산을 **Maple-mcp**로 제공하고, **maplemate skill**이 질문에 맞는 정보 수집과 답변 지침을 제공합니다. 질문 해석과 이미지 판독은 연결한 AI가 담당합니다.

## Getting Started

**Windows 10/11 x64**에서 사용합니다. Codex 또는 Claude Desktop/code와 Nexon openAPI Key(서비스 단계 키 권장)는 별도로 준비하세요.

1. GitHub Releases에서 `MapleMate-1.0.1-windows-x64.zip`을 내려받아 **폴더 전체를 압축 해제**합니다.
2. `MapleMate.exe`를 실행하고 Nexon API Key를 저장합니다.
3. 설정에서 **Codex 연결** 또는 **Claude Desktop 연결**을 누릅니다.
4. 연결한 AI 앱을 재시작하고 질문합니다.

```text
Codex: $maplemate 지금 접속 중인 캐릭터 정보 정리해줘
Claude Desktop: MapleMate로 이 장비를 교체하면 최종뎀 얼마나 오르는지 알려줘
```

이벤트·퀘스트·직업·용어 같은 일반 질문도 할 수 있습니다. 성장 추천에는 캐릭터 수준과 목표·예산·플레이 시간을 반영합니다. API에 없는 최신 정보는 AI의 검색 기능으로 보완합니다.

## 제공하는 정보와 계산

- 공식 KMS **모든 open API**: 캐릭터·장비·스킬·유니온·길드·랭킹·공지·이벤트·강화 이력·콘텐츠 진행 상태 등. 질문에 필요한 항목만 조회하며 API 키 권한과 공식 조회 제한을 따릅니다.
- 캐릭터별 목표·예산·플레이 시간·보스 경험·화면 관찰·API 조회 기록. 출처와 기준 시각을 구분하며 마지막 관찰을 실시간 상태로 표현하지 않습니다.
- 장비 교체 전후 딜 증가율을 최종뎀으로 환산하고, 최종뎀 1%당 메소 비용·강화 기대값·스킬별 가중 효율을 계산합니다. 부족한 입력은 추가로 확인합니다.
- Codex에는 **maplemate skill**를 설치합니다. Claude Desktop은 동일한 지침을 MCP guide와 prompt로 받습니다.

## 데이터와 개인정보

기본 저장 위치는 `%LOCALAPPDATA%\MapleMate`입니다. 기존 `.maplemcp`·`.maplemate` 또는 기존 AI 연결에 지정된 데이터 폴더를 이어받습니다. 명시적 `--data-dir`와 `MAPLEMATE_DATA_DIR`도 지원합니다.

API 키는 Windows DPAPI로 암호화합니다. 요청한 게임 이미지와 조회 정보는 연결한 AI 서비스에 전달하지만, API 키와 로컬 연결 인증정보는 전달하지 않습니다. 게임 이미지 픽셀은 디스크에 저장하지 않습니다. 게임 입력 자동화·메모리 조회·상시 화면 감시는 하지 않습니다.


공식 자료: [Nexon Open API](https://openapi.nexon.com/ko/game/maplestory/), [Windows 화면 캡처](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture), [로컬 MCP 연결](https://modelcontextprotocol.io/docs/develop/connect-local-servers).

## License

MapleMate의 자체 코드와 maplemate skill은 [MIT License](LICENSE)를 따릅니다. Copyright (c) 2026 nullpo0.

외부 라이브러리는 각각의 라이선스를 유지하며, Nexon API 데이터·메이플스토리 이미지·상표 등의 권리는 MIT 적용 범위에 포함되지 않습니다. 이 앱은 Nexon의 공식 제품이나 제휴 서비스가 아닙니다. 데이터 출처: NEXON Open API.

배포 ZIP에는 라이선스 본문, 외부 라이브러리 고지, 앱 소스와 pystray 소스를 포함합니다. 자세한 내용과 재빌드 방법은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 참고하세요.
