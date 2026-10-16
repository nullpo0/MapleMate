# 개발 및 릴리스

## 소스 실행

Windows x64와 Python 3.11 이상이 필요합니다. 일반 사용자에게는 Python이 포함된 릴리스 ZIP을 제공합니다.

```powershell
python scripts/bootstrap.py
.\.venv\Scripts\python.exe -m maplemate tray
```

표준 venv를 직접 만들고 `python -m pip install -e ".[build]"`로 설치해도 됩니다. 개발 명령은 `maplemate tray`, `serve`, `status`, `connect codex`, `connect claude-desktop`입니다. 수동 MCP 등록 예시는 [mcp-config.json](../examples/mcp-config.json)에 있습니다. 평소에는 GUI의 연결 버튼을 사용합니다.

## 구조

```text
src/maplemate/             트레이, 설정, Windows 캡처, named pipe, 로컬 state
src/maplemate/maple_mcp/   stdio MCP, Nexon API, 계산
skills/maplemate/          하나의 skill과 질문별 reference, Codex 메타데이터
scripts/                  설치, 빌드, 패키지 검증, 실제 동작 검증
tests/                    API, state, 연결, 계산, 이미지 전달 테스트
docs/                     개발 문서
.github/workflows/        테스트 및 Windows 릴리스 자동화
```

트레이와 MCP 서버는 인증된 Windows named pipe로 통신합니다. MCP는 stdio로 AI 클라이언트와 연결합니다. 이미지 판독과 답변 생성은 클라이언트 LLM이 담당합니다. 캡처 worker는 한 장 처리 후 종료하며 별도 트레이를 만들지 않습니다.

API 총합에 포함된 스탯과 장비·링크 효과를 중복 합산하지 않습니다. % 적용·미적용 수치는 분리하고 모르는 값을 0으로 채우지 않습니다. 상세 규칙은 [damage](../skills/maplemate/references/damage.md), 매물 비교 흐름은 [equipment](../skills/maplemate/references/equipment.md)를 따릅니다.

## MCP 도구

| 도구 | 역할 |
| --- | --- |
| get_maple_status | API 설정 및 트레이 상태 |
| get_maple_advice_guide | 질문별 정보 수집·계산·성장 지침 |
| capture_game_screen | 게임 창 한 장 또는 지정 영역 캡처 |
| record_screen_observation | 캡처 ID·시각에 연결한 LLM 판독 저장 |
| find_character | API 등록 이름과 닉네임 조건으로 후보 조회 |
| get_player_state | 출처·시각을 포함한 캐릭터별 기록 |
| get_character_information | 캐릭터 공식 API 항목 조회 |
| list_nexon_apis | API 목록·필수 입력·권한 확인 |
| get_nexon_api | 공식 API 카탈로그의 지정 항목 조회 |
| get_upgrade_context | 장비·예산·누락 조건 수집 |
| calculate_upgrade_expectation | 출처가 명시된 강화 규칙의 기대값 |
| get_damage_calculation_context | API·사용자 입력·출처 종합 |
| calculate_final_damage_gain | 전후 스펙의 최종뎀 환산 증가율 |
| calculate_final_damage_option_gain | 기존 옵션 제거를 포함한 교체 효율 |
| calculate_skill_damage_comparison | 스킬별 조건과 점유율 가중 계산 |
| update_player_context | 사용자 입력 갱신·초기화·revision 검사 |
| record_growth_assessment | LLM의 잠정 성장 판단 저장 |

캡처 도구 응답은 Base64와 JSON을 포함하여 900,000 bytes 이하입니다. 작은 PNG는 유지하고 큰 이미지는 JPEG 압축 후 필요 시 축소합니다. `region={x,y,width,height}`는 원래 게임 창 좌표이며 새 프레임에서 해당 영역만 전달합니다. 픽셀은 디스크에 저장하지 않습니다.

state는 SQLite에 캐릭터별로 저장하며 사용자 사실·공식 API·화면 판독·LLM 판단을 구분합니다. `revision`으로 동시 변경을 검사하고 초기화 이전의 캡처/API 응답을 거부합니다. 기존 키·SQLite 기록·앱 연결을 이어받는 마이그레이션 코드는 의도적으로 유지합니다. 이전 관리 skill은 수정 여부를 확인한 뒤 검색 경로 밖으로 백업합니다.

API 카탈로그는 패키지의 `nexon_catalog.json`을 사용합니다. 갱신할 때만 `python -m pip install PyYAML` 후 `python scripts/update_nexon_catalog.py`를 실행합니다. 과거 조회 날짜·계정 전용 권한·필수 ID 등의 제한은 공식 API 조건을 따릅니다. 강화 이력은 현재 확률표가 아닙니다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/smoke_maplemate_mcp.py
.\.venv\Scripts\python.exe scripts/build_release.py
.\.venv\Scripts\python.exe scripts/smoke_maplemate_mcp.py --bundle dist/MapleMate
.\.venv\Scripts\python.exe scripts/smoke_skill_install.py --bundle dist/MapleMate
.\.venv\Scripts\python.exe scripts/smoke_tray.py --bundle dist/MapleMate --settings
```

smoke 검증은 격리된 프로필·데이터와 합성 이미지를 사용합니다. 실제 계정 키나 게임 캡처를 요구하지 않습니다. GUI 검증은 사용자 데스크톱이 있는 Windows 환경에서 실행합니다.

## 릴리스

빌드 의존성이 없다면 `python -m pip install -e ".[build]"`를 먼저 실행합니다. 빌드 결과는 `dist/MapleMate/`, `dist/MapleMate-1.0.2-windows-x64.zip`, `.zip.sha256`입니다. 빌드 스크립트가 버전·동봉 skill·체크섬·ZIP 구조·개인 데이터 제외를 검사합니다. 실행 중인 이전 bundle을 덮어쓰려면 먼저 트레이와 해당 MCP 연결을 종료하거나 `--output-dir dist/new-release`를 사용하세요.

1. `pyproject.toml`, `src/maplemate/__init__.py`, README와 CHANGELOG의 릴리스 버전을 맞춥니다.
2. 테스트와 frozen MCP·skill·트레이 검증을 통과시킵니다.
3. GitHub 저장소에 소스를 올린 뒤 **Windows release**를 수동 실행하여 artifact를 확인합니다.
4. 같은 버전의 태그(`v1.0.2`)를 push하면 ZIP과 체크섬이 첨부된 초안 릴리스가 만들어집니다.
5. 초안 내용을 확인하고 GitHub에서 Publish release를 누릅니다.

일반 push와 pull request에서도 단위 테스트와 소스 stdio 검증을 수행합니다. 릴리스 버전과 태그가 다르면 배포 작업이 빌드 전에 실패합니다.

`.maplemcp`, `.maplemate`, `.cache`, `.venv`, `build`, `dist`, DPAPI 키와 SQLite 데이터는 Git 추적 대상이 아닙니다. 배포 ZIP에는 실행파일·런타임·skill·사용 문서와 라이선스·소스·재빌드 자료를 넣습니다. `.maplemcp`를 삭제하는 방식으로 프로젝트를 정리하지 마세요.

## 라이선스 자료

자체 코드와 skill은 MIT이며 project.license와 LICENSE로 표시합니다. 빌드 시 package_notices.py가 실제 의존성의 버전·라이선스 파일을 수집하고 Python·Tcl·Tk 고지, pystray의 원본 Python 소스와 앱 소스를 동봉합니다. THIRD_PARTY_NOTICES.md의 재빌드 절차도 같은 ZIP에 넣습니다. 수집이 불완전하거나 파일 해시가 바뀌면 릴리스 검증이 실패합니다.

이미 공개된 v1.0.0 태그는 변경하지 않습니다. 라이선스 자료를 추가한 패치 릴리스는 v1.0.1입니다.

## Claude Desktop 연결 경로

client_paths.py의 경로 탐색을 MCP 등록과 기존 데이터 폴더 재사용에 함께 사용합니다. 일반 설치는 APPDATA/Claude, MSIX는 LOCALAPPDATA/Packages/Claude_*/LocalCache/Roaming/Claude의 claude_desktop_config.json을 사용합니다. 패키지 경로가 있으면 일반 경로에 남은 등록 대신 패키지 설정을 확인합니다. 여러 패키지 경로가 있으면 임의로 고르지 않고 등록을 중단합니다. 등록 상태는 설정 파일 검사 결과이며 실제 Claude MCP 연결 성공을 뜻하지 않습니다.
