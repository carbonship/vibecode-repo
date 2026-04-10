# Java Log Trend Monitor

Java 애플리케이션 로그의 Exception 추이를 실시간 모니터링하여, 평소 대비 이상 패턴 발생 시 SMS/이메일/Webhook 알림을 자동 발송하는 경량 분석 시스템입니다.

## 배경

Java 시스템 운영 시 평소에도 다양한 Exception이 지속적으로 발생하여, 실제 크리티컬한 장애와 일상적인 에러를 구분하기 어렵습니다. 이 도구는 **절대 건수가 아닌 "평소 대비 추이 변화"** 를 감지하여 진짜 문제 상황만 알림으로 전달합니다.

## 주요 특징

- **스트리밍 파싱** - 대용량 로그 파일을 메모리에 올리지 않고 라인 단위로 처리
- **파일 오프셋 추적** - 마지막 읽은 위치부터 이어읽기 (중복 처리 방지)
- **로그 로테이션 감지** - inode/파일크기 기반 자동 감지
- **3종 경량 알고리즘 복합 적용** - 각기 다른 이상 패턴을 보완적으로 탐지
- **가중치 기반 복합 스코어링** - Exception 유형/시간대/연속성을 종합한 중요도 산출
- **알림 쿨다운** - 동일 유형 알림 폭주 방지
- **SQLite 상태 영속화** - 재시작 시에도 알고리즘 상태와 히스토리 유지

## 파일 구조

```
log-monitor/
├── config.yaml          # 전체 설정 (로그경로, 알고리즘, 가중치, 알림)
├── log_parser.py        # 스트리밍 기반 Java 로그 파서
├── trend_analyzer.py    # 3종 이상탐지 알고리즘 (EWMA, Z-Score, ROC)
├── scorer.py            # 가중치 기반 복합 스코어링 엔진
├── alert_notifier.py    # SMS/이메일/Webhook 알림 발송
├── state_store.py       # SQLite 기반 상태 저장소
├── log_monitor.py       # 메인 오케스트레이터 (진입점)
├── requirements.txt     # Python 의존성
└── .gitignore
```

## 요구사항

- Python 3.10+
- PyYAML

```bash
pip install -r requirements.txt
```

## 실행 방법

### 데몬 모드 (지속 모니터링)

```bash
python log_monitor.py --config config.yaml --mode daemon
```

설정된 `interval_seconds` 간격(기본 60초)마다 로그 파일을 스캔하고 분석합니다. `SIGTERM` 또는 `SIGINT`(Ctrl+C)로 안전하게 종료됩니다.

### 1회 실행 모드 (cron 연동)

```bash
python log_monitor.py --config config.yaml --mode once
```

1회 분석 후 종료합니다. crontab에 등록하여 주기적 실행이 가능합니다.

```cron
# 매 분 실행 예시
* * * * * cd /path/to/log-monitor && python log_monitor.py -c config.yaml -m once
```

### 리포트 모드

```bash
# 최근 24시간 통계
python log_monitor.py --config config.yaml --mode report --hours 24
```

Exception 유형별 발생 건수와 최대 스코어를 테이블 형태로 출력합니다.

### 설정 검증 모드

```bash
python log_monitor.py --config config.yaml --mode test
```

로그 파일 접근, 알림 채널, 알고리즘 설정을 검증합니다.

## 알고리즘 상세

3종의 경량 통계 알고리즘을 복합 적용하여 각각의 강점으로 서로의 약점을 보완합니다.

### 1. EWMA (지수가중이동평균) - 비중 35%

```
S_t = α × x_t + (1 - α) × S_{t-1}
```

- **원리**: 최근 데이터에 더 큰 가중치를 부여하여 이동평균 계산
- **감지 대상**: 점진적으로 증가하는 추세, 서서히 악화되는 상황
- **복잡도**: O(1) 메모리, O(1) 계산
- **파라미터**: `alpha` (평활계수, 기본 0.3), `threshold_multiplier` (기본 2.0)

### 2. Z-Score (표준편차 기반) - 비중 35%

```
Z = (x - μ) / σ
```

- **원리**: 슬라이딩 윈도우 내 평균·표준편차 대비 현재값의 표준점수 계산
- **감지 대상**: 정상 범위를 명확히 벗어나는 이상치
- **복잡도**: Welford 증분 알고리즘으로 O(1) 업데이트, 수치 안정적
- **파라미터**: `threshold` (기본 2.5), 윈도우 크기는 `baseline_window_count` 사용

### 3. ROC (Rate of Change, 변화율) - 비중 30%

```
ROC% = ((현재값 - 이전값) / 이전값) × 100
```

- **원리**: 이전 윈도우 대비 변화율(%)로 급격한 증감 감지
- **감지 대상**: 갑작스러운 Exception 폭증 (장애 발생 등)
- **복잡도**: O(1) 메모리, O(1) 계산
- **파라미터**: `threshold_percent` (기본 200%)

## 스코어링 체계

```
최종 스코어 = 알고리즘 복합점수 × Exception 유형 가중치 × 시간대 가중치 × 연속 부스트
                 (0~1)            (0.2~1.0)           (0.8~1.0)       (1.0~1.5)
```

### Exception 유형별 가중치

| 심각도 | 가중치 | 예시 |
|--------|--------|------|
| Critical | 1.0 | `OutOfMemoryError`, `StackOverflowError`, `NoClassDefFoundError` |
| High | 0.8 | `NullPointerException`, `SQLException`, `IOException`, `TimeoutException` |
| Medium | 0.5 | `IllegalArgumentException`, `IllegalStateException` |
| Low | 0.2 | `NumberFormatException`, `ParseException` |
| 미분류 | 0.3 | config에 등록되지 않은 Exception |

### 심각도 판정 기준

| 레벨 | 스코어 범위 | 알림 |
|------|------------|------|
| CRITICAL | 0.85 이상 | 즉시 발송 |
| HIGH | 0.60 이상 | 연속 N회 초과 시 발송 |
| MEDIUM | 0.42 이상 | 기록만 |
| LOW | 0.24 이상 | 기록만 |
| NORMAL | 0.24 미만 | - |

### 알림 조건

- **CRITICAL 스코어**: 즉시 알림 발송
- **HIGH 스코어**: `consecutive_anomaly_count`(기본 2) 연속 초과 시 발송
- **쿨다운**: 동일 유형 알림은 `alert_cooldown_seconds`(기본 300초) 간격 제한

## 설정 가이드 (config.yaml)

### 로그 파일 설정

```yaml
log_files:
  paths:
    - /var/log/java-app/application.log
    - /var/log/java-app/error.log
  timestamp_formats:
    - "%Y-%m-%d %H:%M:%S,%f"    # Log4j 기본 포맷
  encoding: utf-8
```

### 알림 채널 설정

```yaml
alerting:
  email:
    enabled: true
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    use_tls: true
    username: "your-email@gmail.com"
    password: "${SMTP_PASSWORD}"          # 환경변수 참조
    from_address: "monitor@company.com"
    to_addresses:
      - "ops-team@company.com"

  sms:
    enabled: true
    account_sid: "${TWILIO_ACCOUNT_SID}"  # 환경변수 참조
    auth_token: "${TWILIO_AUTH_TOKEN}"
    from_number: "+1234567890"
    to_numbers:
      - "+0987654321"

  webhook:
    enabled: true
    urls:
      - "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
```

비밀값은 반드시 `${ENV_VAR}` 형식으로 환경변수를 참조하세요.

## 운영 도입 시 검토사항

### 1. 로그 포맷 호환성
- 현재 `타임스탬프 [LEVEL] 메시지` 형태의 표준 Java 로그 포맷 기준
- Log4j2, Logback 등 실제 사용 중인 로그 프레임워크의 패턴에 맞춰 `log_parser.py`의 `LOG_LINE_PATTERN` 정규식 조정 필요
- JSON 형식 로그(구조화 로깅)를 사용하는 경우 별도 JSON 파서 추가 필요

### 2. 임계값 튜닝 (가장 중요)
- **초기 2~4주간은 알림 없이 데이터만 수집**하여 baseline 확보 권장
- `report` 모드로 통계를 확인하면서 점진적으로 임계값 조정
- 환경별로 Exception 발생 패턴이 다르므로 각 환경에 맞는 튜닝 필요

### 3. 로그 로테이션
- inode 기반 + 파일 크기 기반 로테이션 감지 구현
- `copytruncate` 방식의 logrotate도 지원
- 날짜별 로그 파일(`app-2024-01-15.log`)은 `glob_patterns` 설정 활용

### 4. 성능
- 스트리밍 방식으로 메모리 문제 없음
- 수 GB 이상 파일의 초기 풀스캔 시 시간이 걸릴 수 있음 → 첫 실행 전 오프셋을 파일 끝으로 설정 고려
- NFS/원격 마운트 로그 파일의 경우 I/O 지연 고려

### 5. 다중 서버 환경
- 각 서버에 독립 실행하고, 알림은 중앙 Webhook(Slack/Teams)으로 집중하는 구조 권장
- 서버 식별을 위해 알림 메시지에 호스트명 추가 검토

### 6. 보안
- SMTP 패스워드, Twilio 토큰 등은 환경변수로 관리
- `log_monitor_state.db` 파일 권한 제한 (600)

### 7. 프로세스 관리

systemd 서비스 등록 예시:

```ini
[Unit]
Description=Java Log Trend Monitor
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/log-monitor
ExecStart=/usr/bin/python3 log_monitor.py -c config.yaml -m daemon
Restart=always
RestartSec=10
Environment=SMTP_PASSWORD=your-password

[Install]
WantedBy=multi-user.target
```

### 8. 확장 고려
- **Grafana 대시보드**: SQLite 데이터를 Grafana + SQLite datasource로 시각화
- **Prometheus 연동**: `/metrics` 엔드포인트 추가하여 기존 모니터링 체계 통합
- **커스텀 Exception**: `config.yaml`의 `exception_weights`에 사내 Exception 클래스 등록
