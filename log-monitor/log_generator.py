#!/usr/bin/env python3
"""
Java Log Generator - 로컬 테스트용 로그 재생/생성기

운영 로그 파일을 가져와서 실시간으로 로그가 쌓이는 환경을 시뮬레이션합니다.

모드:
  1) replay    - 실제 운영 로그 파일을 타임스탬프 간격대로 재생
  2) synthetic - 현실적인 Java 로그를 패턴 기반으로 자동 생성

사용법:
  # 운영 로그 1주일치를 60배속으로 재생 (1주 → 약 2.8시간)
  python log_generator.py replay \\
      --source /path/to/prod-logs/ \\
      --output ./test-logs/application.log \\
      --speed 60

  # 100배속 재생 (1주 → 약 1.7시간)
  python log_generator.py replay \\
      --source ./prod-week-logs/ \\
      --output ./test-logs/application.log \\
      --speed 100

  # 합성 로그 생성 (정상 패턴 + 주기적 이상 주입)
  python log_generator.py synthetic \\
      --output ./test-logs/application.log \\
      --duration 3600 \\
      --anomaly-interval 300

  # 실행 중 수동 스파이크 주입: 별도 터미널에서
  kill -USR1 <generator_pid>
"""

import argparse
import glob
import json
import math
import os
import random
import re
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


# ============================================================
# 공통 상수
# ============================================================

JAVA_LOG_LEVELS = ['TRACE', 'DEBUG', 'INFO', 'WARN', 'ERROR']

TIMESTAMP_PATTERN = re.compile(
    r'^(\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}[,.\d]*)'
)

TIMESTAMP_FORMATS = [
    '%Y-%m-%d %H:%M:%S,%f',
    '%Y-%m-%d %H:%M:%S.%f',
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%dT%H:%M:%S',
    '%Y-%m-%dT%H:%M:%S.%f',
]

# 현실적인 Java 클래스/패키지 구조
PACKAGES = [
    'com.example.order.service',
    'com.example.payment.gateway',
    'com.example.user.auth',
    'com.example.product.catalog',
    'com.example.inventory.manager',
    'com.example.notification.sender',
    'com.example.api.controller',
    'com.example.batch.scheduler',
    'com.example.cache.redis',
    'com.example.db.repository',
]

CLASSES = [
    'OrderService', 'PaymentGateway', 'AuthService',
    'ProductCatalog', 'InventoryManager', 'NotificationSender',
    'ApiController', 'BatchScheduler', 'RedisCache',
    'UserRepository', 'TransactionManager', 'SessionHandler',
]

METHODS = [
    'process', 'execute', 'handle', 'validate', 'init',
    'create', 'update', 'delete', 'find', 'save',
    'connect', 'disconnect', 'retry', 'transform', 'publish',
]

# Exception 정의: (클래스, 메시지, 스택트레이스 깊이)
NORMAL_EXCEPTIONS = [
    ('java.lang.NumberFormatException', 'For input string: "{}"', 3),
    ('java.text.ParseException', 'Unparseable date: "{}"', 3),
    ('javax.validation.ValidationException', 'Field {} must not be null', 4),
    ('com.fasterxml.jackson.core.JsonParseException', 'Unexpected character', 4),
    ('java.lang.IllegalArgumentException', 'Invalid parameter: {}', 3),
]

MODERATE_EXCEPTIONS = [
    ('java.lang.NullPointerException', 'Cannot invoke method on null object', 5),
    ('java.sql.SQLException', 'Connection pool exhausted, active={}, max={}', 6),
    ('java.io.IOException', 'Connection reset by peer', 4),
    ('java.net.SocketTimeoutException', 'Read timed out after {}ms', 5),
    ('java.util.ConcurrentModificationException', 'HashMap modified during iteration', 4),
    ('org.springframework.dao.DataAccessException', 'PreparedStatement callback failed', 6),
]

CRITICAL_EXCEPTIONS = [
    ('java.lang.OutOfMemoryError', 'Java heap space', 8),
    ('java.lang.OutOfMemoryError', 'GC overhead limit exceeded', 8),
    ('java.lang.StackOverflowError', 'in recursive call', 15),
    ('java.lang.NoClassDefFoundError', 'Could not initialize class {}', 6),
    ('java.lang.ClassNotFoundException', '{}', 5),
]

# 정상 로그 메시지
NORMAL_MESSAGES = [
    ('INFO', 'Request processed successfully in {}ms'),
    ('INFO', 'User {} logged in from {}'),
    ('INFO', 'Order {} created, total={}'),
    ('INFO', 'Cache hit for key: {}'),
    ('INFO', 'Scheduled task completed: {} items processed'),
    ('DEBUG', 'SQL query executed in {}ms: SELECT * FROM {}'),
    ('DEBUG', 'HTTP {} {} - {} {}ms'),
    ('WARN', 'Slow query detected: {}ms for table {}'),
    ('WARN', 'Retry attempt {}/3 for service {}'),
    ('WARN', 'Session {} expired, forcing re-authentication'),
]


# ============================================================
# 유틸리티 함수
# ============================================================

def parse_timestamp(line: str) -> datetime | None:
    """로그 라인에서 타임스탬프 추출"""
    m = TIMESTAMP_PATTERN.match(line)
    if not m:
        return None
    ts_str = m.group(1)
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None


def ensure_output_dir(output_path: str):
    """출력 디렉토리 생성"""
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)


def generate_stacktrace(exception_class: str, depth: int) -> list[str]:
    """현실적인 Java 스택트레이스 생성"""
    lines = []
    for i in range(depth):
        pkg = random.choice(PACKAGES)
        cls = random.choice(CLASSES)
        method = random.choice(METHODS)
        line_no = random.randint(30, 500)
        lines.append(f'\tat {pkg}.{cls}.{method}({cls}.java:{line_no})')

    # 프레임워크 레이어 추가
    framework_frames = [
        '\tat org.springframework.web.servlet.FrameworkServlet.service(FrameworkServlet.java:897)',
        '\tat javax.servlet.http.HttpServlet.service(HttpServlet.java:750)',
        '\tat org.apache.catalina.core.ApplicationFilterChain.doFilter(ApplicationFilterChain.java:166)',
        '\tat org.springframework.aop.framework.ReflectiveMethodInvocation.proceed(ReflectiveMethodInvocation.java:186)',
        '\tat java.base/java.util.concurrent.ThreadPoolExecutor.runWorker(ThreadPoolExecutor.java:1128)',
        '\tat java.base/java.lang.Thread.run(Thread.java:829)',
    ]
    lines.extend(random.sample(framework_frames, min(3, len(framework_frames))))
    return lines


# ============================================================
# Replay 모드: 운영 로그 파일 재생
# ============================================================

class LogReplayer:
    """
    운영 로그 파일을 읽어서 타임스탬프 간격대로 재생.

    1주일 로그를 speed 배속으로 압축:
      speed=1    → 실시간 (1주일 걸림)
      speed=60   → 1분이 1초 (1주 → 약 2.8시간)
      speed=100  → (1주 → 약 1.7시간)
      speed=600  → (1주 → 약 17분)
      speed=6000 → (1주 → 약 1.7분)
    """

    def __init__(
        self,
        source_paths: list[str],
        output_path: str,
        speed: float = 60.0,
        anomaly_inject: bool = True,
    ):
        self.source_paths = source_paths
        self.output_path = output_path
        self.speed = speed
        self.anomaly_inject = anomaly_inject
        self.running = True
        self.spike_requested = False

        # USR1 시그널로 수동 스파이크 주입
        signal.signal(signal.SIGUSR1, self._handle_spike_signal)
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)

    def _handle_spike_signal(self, signum, frame):
        print(f"\n[!] USR1 수신 - 다음 구간에서 Exception 스파이크를 주입합니다")
        self.spike_requested = True

    def _handle_stop(self, signum, frame):
        print(f"\n[*] 종료 시그널 수신, 안전하게 종료합니다...")
        self.running = False

    def collect_source_files(self) -> list[str]:
        """소스 로그 파일 수집 (정렬)"""
        files = []
        for path in self.source_paths:
            if os.path.isfile(path):
                files.append(path)
            elif os.path.isdir(path):
                # 디렉토리 내 .log 파일 수집
                for ext in ['*.log', '*.log.*', '*.txt']:
                    files.extend(glob.glob(os.path.join(path, ext)))
                    files.extend(glob.glob(os.path.join(path, '**', ext), recursive=True))
        return sorted(set(files))

    def run(self):
        source_files = self.collect_source_files()
        if not source_files:
            print(f"[ERROR] 소스 로그 파일을 찾을 수 없습니다: {self.source_paths}")
            sys.exit(1)

        total_size = sum(os.path.getsize(f) for f in source_files)
        print(f"[*] Replay 모드 시작")
        print(f"    소스 파일: {len(source_files)}개 (총 {total_size / 1024 / 1024:.1f}MB)")
        print(f"    출력 파일: {self.output_path}")
        print(f"    속도 배율: {self.speed}x")
        print(f"    수동 스파이크: kill -USR1 {os.getpid()}")
        print()

        ensure_output_dir(self.output_path)

        prev_ts = None
        lines_written = 0
        buffer = []   # 동일 타임스탬프 라인 버퍼

        with open(self.output_path, 'a', encoding='utf-8') as out:
            for src_file in source_files:
                if not self.running:
                    break

                print(f"[>] 재생 중: {src_file}")

                try:
                    with open(src_file, 'r', encoding='utf-8', errors='replace') as f:
                        for line in f:
                            if not self.running:
                                break

                            ts = parse_timestamp(line)

                            if ts and prev_ts and ts > prev_ts:
                                # 이전 버퍼 플러시
                                if buffer:
                                    out.write(''.join(buffer))
                                    out.flush()
                                    lines_written += len(buffer)
                                    buffer = []

                                # 타임스탬프 간격만큼 대기 (speed 배속)
                                gap = (ts - prev_ts).total_seconds()
                                sleep_time = gap / self.speed
                                # 최대 5초까지만 대기 (너무 긴 공백 방지)
                                sleep_time = min(sleep_time, 5.0)
                                if sleep_time > 0.001:
                                    time.sleep(sleep_time)

                                # 스파이크 주입 체크
                                if self.spike_requested:
                                    spike_lines = self._generate_spike(ts)
                                    out.write(''.join(spike_lines))
                                    out.flush()
                                    lines_written += len(spike_lines)
                                    self.spike_requested = False
                                    print(f"    [!] 스파이크 주입 완료: {len(spike_lines)}줄")

                            if ts:
                                # 타임스탬프를 현재 시각으로 치환
                                now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
                                line = TIMESTAMP_PATTERN.sub(now_str, line, count=1)
                                prev_ts = ts

                            buffer.append(line)

                            # 주기적 상태 출력
                            if lines_written % 5000 == 0 and lines_written > 0:
                                print(
                                    f"    [{datetime.now().strftime('%H:%M:%S')}] "
                                    f"{lines_written:,}줄 기록됨"
                                )

                except Exception as e:
                    print(f"[WARN] 파일 읽기 오류 ({src_file}): {e}")

            # 마지막 버퍼 플러시
            if buffer:
                out.write(''.join(buffer))
                out.flush()
                lines_written += len(buffer)

        print(f"\n[*] Replay 완료: 총 {lines_written:,}줄 기록됨")

    def _generate_spike(self, base_ts: datetime) -> list[str]:
        """Exception 스파이크 생성 (50~100건)"""
        lines = []
        spike_count = random.randint(50, 100)
        # 주로 특정 Exception에 집중 (현실적 장애 시뮬레이션)
        primary_exc = random.choice(CRITICAL_EXCEPTIONS + MODERATE_EXCEPTIONS)

        for i in range(spike_count):
            ts = datetime.now() + timedelta(milliseconds=i * 50)
            ts_str = ts.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
            thread = f"http-nio-8080-exec-{random.randint(1, 50)}"
            pkg = random.choice(PACKAGES)

            exc_class, exc_msg, depth = primary_exc
            msg = exc_msg.format(*[random.randint(1, 9999) for _ in range(exc_msg.count('{}'))])

            lines.append(f"{ts_str} ERROR [{thread}] {pkg} - {exc_class}: {msg}\n")
            for st_line in generate_stacktrace(exc_class, depth):
                lines.append(st_line + '\n')

            # 간헐적으로 다른 Exception도 섞기
            if random.random() < 0.2:
                other_exc = random.choice(MODERATE_EXCEPTIONS)
                exc_class2, exc_msg2, depth2 = other_exc
                msg2 = exc_msg2.format(*[random.randint(1, 9999) for _ in range(exc_msg2.count('{}'))])
                lines.append(f"{ts_str} ERROR [{thread}] {pkg} - {exc_class2}: {msg2}\n")
                for st_line in generate_stacktrace(exc_class2, depth2):
                    lines.append(st_line + '\n')

        return lines


# ============================================================
# Synthetic 모드: 패턴 기반 로그 자동 생성
# ============================================================

class LogSynthesizer:
    """
    현실적인 Java 로그를 패턴 기반으로 자동 생성.

    시간대별 로그량 변화:
      00-06시: 야간 배치 (적은 로그, 간헐적 배치 에러)
      06-09시: 서비스 기동 (중간, 초기화 에러 가능)
      09-12시: 오전 피크 (많은 로그)
      12-14시: 점심 (약간 감소)
      14-18시: 오후 피크 (많은 로그)
      18-24시: 저녁 (점차 감소)

    Exception 발생 패턴:
      - 기본 노이즈: 항상 소량 발생 (ValidationException 등)
      - 시간대 연동: 피크 시간에 비례하여 증가
      - 이상 주입: 설정된 간격마다 스파이크 자동 발생
    """

    def __init__(
        self,
        output_path: str,
        duration_seconds: int = 3600,
        anomaly_interval: int = 300,
        base_rate: float = 10.0,
    ):
        self.output_path = output_path
        self.duration = duration_seconds
        self.anomaly_interval = anomaly_interval  # 이상 주입 간격 (초)
        self.base_rate = base_rate  # 초당 기본 로그 생성률
        self.running = True
        self.spike_requested = False
        self.anomaly_count = 0

        signal.signal(signal.SIGUSR1, self._handle_spike_signal)
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)

    def _handle_spike_signal(self, signum, frame):
        print(f"\n[!] USR1 수신 - 즉시 스파이크를 주입합니다")
        self.spike_requested = True

    def _handle_stop(self, signum, frame):
        print(f"\n[*] 종료 시그널 수신")
        self.running = False

    def get_hour_multiplier(self, hour: int) -> float:
        """시간대별 로그량 배율"""
        profile = {
            0: 0.2, 1: 0.15, 2: 0.1, 3: 0.1, 4: 0.15, 5: 0.2,
            6: 0.4, 7: 0.6, 8: 0.8,
            9: 1.0, 10: 1.2, 11: 1.1,
            12: 0.8, 13: 0.9,
            14: 1.1, 15: 1.2, 16: 1.0, 17: 0.9,
            18: 0.7, 19: 0.5, 20: 0.4, 21: 0.35, 22: 0.3, 23: 0.25,
        }
        return profile.get(hour, 0.5)

    def get_exception_rate(self, hour: int) -> float:
        """시간대별 Exception 발생 확률 (전체 로그 대비)"""
        base = 0.03  # 기본 3%
        multiplier = self.get_hour_multiplier(hour)
        return base * multiplier

    def generate_normal_log(self) -> str:
        """정상 로그 라인 1건 생성"""
        now = datetime.now()
        ts = now.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
        level, template = random.choice(NORMAL_MESSAGES)
        thread = f"http-nio-8080-exec-{random.randint(1, 50)}"
        pkg = random.choice(PACKAGES)

        # 템플릿 파라미터 채우기
        param_count = template.count('{}')
        params = []
        for _ in range(param_count):
            params.append(random.choice([
                str(random.randint(1, 9999)),
                f"192.168.1.{random.randint(1, 254)}",
                f"user_{random.randint(1000, 9999)}",
                random.choice(['GET', 'POST', 'PUT', 'DELETE']),
                random.choice(['users', 'orders', 'products', 'payments']),
                f"ORD-{random.randint(10000, 99999)}",
            ]))
        message = template.format(*params)

        return f"{ts} {level} [{thread}] {pkg} - {message}\n"

    def generate_exception_log(self, severity: str = 'normal') -> list[str]:
        """Exception 로그 (메시지 + 스택트레이스) 생성"""
        if severity == 'critical':
            exc_class, exc_msg, depth = random.choice(CRITICAL_EXCEPTIONS)
        elif severity == 'moderate':
            exc_class, exc_msg, depth = random.choice(MODERATE_EXCEPTIONS)
        else:
            exc_class, exc_msg, depth = random.choice(NORMAL_EXCEPTIONS)

        now = datetime.now()
        ts = now.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
        thread = f"http-nio-8080-exec-{random.randint(1, 50)}"
        pkg = random.choice(PACKAGES)

        params = [random.choice([
            str(random.randint(1, 9999)),
            f"com.example.{random.choice(CLASSES)}",
            '2024-13-45',
            '{invalid json}',
        ]) for _ in range(exc_msg.count('{}'))]
        message = exc_msg.format(*params)

        lines = [f"{ts} ERROR [{thread}] {pkg} - {exc_class}: {message}\n"]
        for st_line in generate_stacktrace(exc_class, depth):
            lines.append(st_line + '\n')

        # 30% 확률로 Caused by 추가
        if random.random() < 0.3:
            caused_exc = random.choice(NORMAL_EXCEPTIONS + MODERATE_EXCEPTIONS)
            c_class, c_msg, c_depth = caused_exc
            c_params = [str(random.randint(1, 9999)) for _ in range(c_msg.count('{}'))]
            c_message = c_msg.format(*c_params)
            lines.append(f"Caused by: {c_class}: {c_message}\n")
            for st_line in generate_stacktrace(c_class, min(c_depth, 3)):
                lines.append(st_line + '\n')

        return lines

    def generate_spike(self) -> list[str]:
        """이상 스파이크 생성: 30~80건의 Exception 집중 발생"""
        self.anomaly_count += 1
        lines = []
        spike_count = random.randint(30, 80)
        severity = random.choice(['critical', 'moderate', 'moderate'])

        # 동일 Exception 유형이 반복되는 것이 현실적
        if severity == 'critical':
            primary = random.choice(CRITICAL_EXCEPTIONS)
        else:
            primary = random.choice(MODERATE_EXCEPTIONS)

        print(
            f"    [ANOMALY #{self.anomaly_count}] "
            f"{primary[0]} x{spike_count}건 스파이크 주입"
        )

        for i in range(spike_count):
            exc_class, exc_msg, depth = primary
            now = datetime.now()
            ts = now.strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
            thread = f"http-nio-8080-exec-{random.randint(1, 50)}"
            pkg = random.choice(PACKAGES)
            params = [str(random.randint(1, 9999)) for _ in range(exc_msg.count('{}'))]
            message = exc_msg.format(*params)

            lines.append(f"{ts} ERROR [{thread}] {pkg} - {exc_class}: {message}\n")
            for st_line in generate_stacktrace(exc_class, depth):
                lines.append(st_line + '\n')

            # 20% 확률로 연쇄 Exception
            if random.random() < 0.2:
                other = random.choice(MODERATE_EXCEPTIONS)
                lines.extend(self.generate_exception_log('moderate'))

        return lines

    def run(self):
        ensure_output_dir(self.output_path)

        print(f"[*] Synthetic 모드 시작")
        print(f"    출력 파일: {self.output_path}")
        print(f"    실행 시간: {self.duration}초")
        print(f"    기본 로그율: {self.base_rate}/초")
        print(f"    이상 주입 간격: {self.anomaly_interval}초")
        print(f"    수동 스파이크: kill -USR1 {os.getpid()}")
        print()

        start_time = time.time()
        last_anomaly_time = start_time
        lines_written = 0
        exceptions_written = 0

        with open(self.output_path, 'a', encoding='utf-8') as out:
            while self.running:
                elapsed = time.time() - start_time
                if elapsed >= self.duration:
                    break

                now = datetime.now()
                hour = now.hour
                multiplier = self.get_hour_multiplier(hour)
                exc_rate = self.get_exception_rate(hour)

                # 이번 틱에서 생성할 로그 수 (포아송 분포 근사)
                rate = self.base_rate * multiplier
                count = max(1, int(random.gauss(rate, rate * 0.3)))

                for _ in range(count):
                    if random.random() < exc_rate:
                        # Exception 로그
                        severity_roll = random.random()
                        if severity_roll < 0.05:
                            severity = 'moderate'
                        elif severity_roll < 0.01:
                            severity = 'critical'
                        else:
                            severity = 'normal'
                        exc_lines = self.generate_exception_log(severity)
                        out.write(''.join(exc_lines))
                        exceptions_written += 1
                    else:
                        # 정상 로그
                        out.write(self.generate_normal_log())
                    lines_written += 1

                # 자동 이상 주입
                time_since_anomaly = time.time() - last_anomaly_time
                if time_since_anomaly >= self.anomaly_interval:
                    spike_lines = self.generate_spike()
                    out.write(''.join(spike_lines))
                    out.flush()
                    lines_written += len(spike_lines)
                    last_anomaly_time = time.time()

                # 수동 스파이크
                if self.spike_requested:
                    spike_lines = self.generate_spike()
                    out.write(''.join(spike_lines))
                    out.flush()
                    lines_written += len(spike_lines)
                    self.spike_requested = False

                out.flush()

                # 1초 간격
                time.sleep(1.0)

                # 상태 출력 (10초마다)
                if int(elapsed) % 10 == 0 and int(elapsed) > 0:
                    remaining = self.duration - elapsed
                    print(
                        f"    [{now.strftime('%H:%M:%S')}] "
                        f"경과={int(elapsed)}s, "
                        f"잔여={int(remaining)}s, "
                        f"로그={lines_written:,}, "
                        f"예외={exceptions_written}, "
                        f"이상주입={self.anomaly_count}회"
                    )

        elapsed = time.time() - start_time
        print(f"\n[*] Synthetic 완료")
        print(f"    실행 시간: {elapsed:.1f}초")
        print(f"    총 로그: {lines_written:,}줄")
        print(f"    총 Exception: {exceptions_written}건")
        print(f"    이상 주입: {self.anomaly_count}회")


# ============================================================
# 메인
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='Java Log Generator - 로컬 테스트용 로그 재생/생성기',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:

  # 운영 로그 60배속 재생
  python log_generator.py replay \\
      --source /path/to/prod-logs/ \\
      --output ./test-logs/application.log \\
      --speed 60

  # 합성 로그 1시간 생성 (5분마다 이상 주입)
  python log_generator.py synthetic \\
      --output ./test-logs/application.log \\
      --duration 3600 \\
      --anomaly-interval 300

  # 실행 중 수동 스파이크: kill -USR1 <pid>

  # 동시에 모니터 실행 (별도 터미널):
  python log_monitor.py -c config_test.yaml -m daemon
        """
    )

    subparsers = parser.add_subparsers(dest='mode', help='실행 모드')
    subparsers.required = True

    # --- replay 모드 ---
    replay_parser = subparsers.add_parser(
        'replay', help='운영 로그 파일을 타임스탬프 기반으로 재생'
    )
    replay_parser.add_argument(
        '--source', '-s', required=True, nargs='+',
        help='소스 로그 파일 또는 디렉토리 경로 (여러 개 가능)'
    )
    replay_parser.add_argument(
        '--output', '-o', required=True,
        help='출력 로그 파일 경로'
    )
    replay_parser.add_argument(
        '--speed', type=float, default=60.0,
        help='재생 속도 배율 (기본: 60, 즉 1분→1초)'
    )

    # --- synthetic 모드 ---
    synth_parser = subparsers.add_parser(
        'synthetic', help='패턴 기반 Java 로그 자동 생성'
    )
    synth_parser.add_argument(
        '--output', '-o', required=True,
        help='출력 로그 파일 경로'
    )
    synth_parser.add_argument(
        '--duration', '-d', type=int, default=3600,
        help='실행 시간 (초, 기본: 3600 = 1시간)'
    )
    synth_parser.add_argument(
        '--anomaly-interval', type=int, default=300,
        help='자동 이상 주입 간격 (초, 기본: 300 = 5분)'
    )
    synth_parser.add_argument(
        '--base-rate', type=float, default=10.0,
        help='초당 기본 로그 생성률 (기본: 10)'
    )

    args = parser.parse_args()

    if args.mode == 'replay':
        replayer = LogReplayer(
            source_paths=args.source,
            output_path=args.output,
            speed=args.speed,
        )
        replayer.run()

    elif args.mode == 'synthetic':
        synthesizer = LogSynthesizer(
            output_path=args.output,
            duration_seconds=args.duration,
            anomaly_interval=args.anomaly_interval,
            base_rate=args.base_rate,
        )
        synthesizer.run()


if __name__ == '__main__':
    main()
