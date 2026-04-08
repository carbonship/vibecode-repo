#!/usr/bin/env python3
"""
Java Log Trend Monitor - 메인 오케스트레이터

실행 모드:
  1) daemon  - 백그라운드 데몬으로 지속 모니터링
  2) once    - 1회 분석 후 종료 (cron 연동용)
  3) report  - 최근 통계 리포트 출력
  4) test    - 설정 검증 및 알림 테스트

사용법:
  python log_monitor.py --config config.yaml --mode daemon
  python log_monitor.py --config config.yaml --mode once
  python log_monitor.py --config config.yaml --mode report --hours 24
  python log_monitor.py --config config.yaml --mode test
"""

import argparse
import glob
import json
import logging
import logging.handlers
import os
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

import yaml

from log_parser import JavaLogParser, LogEntry
from trend_analyzer import TrendAnalyzer
from scorer import CompositeScorer
from alert_notifier import AlertNotifier
from state_store import StateStore


class LogMonitor:
    """메인 모니터링 오케스트레이터"""

    def __init__(self, config_path: str):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        self._setup_logging()
        self.logger = logging.getLogger('log_monitor')

        # 모듈 초기화
        log_cfg = self.config.get('log_files', {})
        self.parser = JavaLogParser(
            timestamp_formats=log_cfg.get('timestamp_formats', ['%Y-%m-%d %H:%M:%S']),
            encoding=log_cfg.get('encoding', 'utf-8'),
        )

        state_cfg = self.config.get('state', {})
        self.state = StateStore(
            db_path=state_cfg.get('db_path', './log_monitor_state.db'),
            retention_days=state_cfg.get('retention_days', 30),
        )

        # Exception 유형별 TrendAnalyzer (독립적으로 추이 추적)
        self.analyzers: dict[str, TrendAnalyzer] = {}
        self.scorer = CompositeScorer(self.config)
        self.notifier = AlertNotifier(self.config)

        analysis_cfg = self.config.get('analysis', {})
        self.interval = analysis_cfg.get('interval_seconds', 60)
        self.min_data_points = analysis_cfg.get('min_data_points', 10)

        self.running = True

    def _setup_logging(self):
        log_cfg = self.config.get('monitoring_log', {})
        level = getattr(logging, log_cfg.get('level', 'INFO').upper(), logging.INFO)

        root_logger = logging.getLogger()
        root_logger.setLevel(level)

        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # 콘솔 핸들러
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root_logger.addHandler(console)

        # 파일 핸들러 (로테이션)
        log_file = log_cfg.get('file', './log_monitor.log')
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=log_cfg.get('max_bytes', 10485760),
            backupCount=log_cfg.get('backup_count', 3),
            encoding='utf-8',
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    def _get_log_files(self) -> list[str]:
        """모니터링 대상 로그 파일 목록 수집"""
        files = set()
        log_cfg = self.config.get('log_files', {})

        for path in log_cfg.get('paths', []):
            if os.path.isfile(path):
                files.add(path)

        for pattern in log_cfg.get('glob_patterns', []):
            files.update(glob.glob(pattern))

        return sorted(files)

    def _get_or_create_analyzer(self, exception_type: str) -> TrendAnalyzer:
        """Exception 유형별 분석기 (lazy 생성 + 상태 복원)"""
        if exception_type not in self.analyzers:
            analyzer = TrendAnalyzer(self.config)
            # 이전 상태 복원
            saved_state = self.state.get_algorithm_state(f"analyzer:{exception_type}")
            if saved_state:
                analyzer.load_state(saved_state)
            self.analyzers[exception_type] = analyzer
        return self.analyzers[exception_type]

    def scan_once(self) -> dict:
        """
        1회 스캔: 모든 로그 파일을 순회하며 새 Exception 수집 및 분석

        Returns:
            {exception_type: count} 윈도우 내 집계 결과
        """
        log_files = self._get_log_files()
        if not log_files:
            self.logger.warning("모니터링 대상 로그 파일이 없습니다")
            return {}

        # 윈도우 내 Exception 집계
        window_counts: dict[str, int] = defaultdict(int)

        for filepath in log_files:
            if not os.path.exists(filepath):
                self.logger.debug(f"파일 없음: {filepath}")
                continue

            # 오프셋 및 inode 확인
            saved_offset, saved_inode = self.state.get_file_offset(filepath)
            current_inode = self.parser.get_file_inode(filepath)

            # 로그 로테이션 감지: inode가 바뀌었으면 처음부터
            if saved_inode != 0 and current_inode != saved_inode:
                self.logger.info(f"로그 로테이션 감지: {filepath}")
                saved_offset = 0

            # 스트리밍 파싱
            last_offset = saved_offset
            entry_count = 0

            try:
                for entry, offset in self.parser.stream_entries(filepath, saved_offset):
                    window_counts[entry.exception_type] += 1
                    last_offset = offset
                    entry_count += 1
            except Exception as e:
                self.logger.error(f"파일 파싱 오류 ({filepath}): {e}")
                continue

            # 오프셋 업데이트
            if last_offset > saved_offset:
                self.state.set_file_offset(filepath, last_offset, current_inode)
                self.logger.debug(
                    f"{filepath}: {entry_count}건 처리, "
                    f"오프셋 {saved_offset} → {last_offset}"
                )

        return dict(window_counts)

    def analyze_and_alert(self, window_counts: dict[str, int]):
        """Exception 집계 결과를 분석하고 필요시 알림 발송"""
        if not window_counts:
            return

        now = datetime.now()

        for exc_type, count in window_counts.items():
            # 1) 추이 분석
            analyzer = self._get_or_create_analyzer(exc_type)
            algo_results, algo_score = analyzer.analyze(float(count))

            # 2) 복합 스코어링
            score_result = self.scorer.score(
                exception_type=exc_type,
                count_in_window=count,
                algorithm_score=algo_score,
                timestamp=now,
            )

            # 3) 통계 기록
            self.state.record_exception_stats(
                exception_type=exc_type,
                count=count,
                score=score_result.final_score,
                severity=score_result.severity_level,
            )

            # 4) 알고리즘 상태 저장
            self.state.set_algorithm_state(
                f"analyzer:{exc_type}",
                analyzer.get_state()
            )

            # 5) 로그 출력 (MEDIUM 이상만)
            if score_result.severity_level in ('CRITICAL', 'HIGH', 'MEDIUM'):
                self.logger.warning(
                    f"[{score_result.severity_level}] {exc_type}: "
                    f"건수={count}, 스코어={score_result.final_score:.3f} "
                    f"({score_result.detail})"
                )

            # 6) 알림 발송 판단
            if self.scorer.should_alert(score_result):
                algo_detail = '\n'.join(
                    f"  - {r.name}: score={r.score:.3f}, "
                    f"현재={r.current_value:.1f}, "
                    f"예상={r.expected_value:.1f} "
                    f"({'이상' if r.is_anomaly else '정상'}) "
                    f"[{r.detail}]"
                    for r in algo_results
                )
                sent = self.notifier.send_alert(score_result, algo_detail)
                if sent:
                    self.state.record_alert(
                        exception_type=exc_type,
                        severity=score_result.severity_level,
                        score=score_result.final_score,
                        channels='email,sms,webhook',
                        detail=algo_detail,
                    )
                    self.scorer.reset_consecutive(exc_type)
                    self.logger.info(
                        f"알림 발송 완료: [{score_result.severity_level}] {exc_type}"
                    )

    def run_daemon(self):
        """데몬 모드: 주기적으로 스캔 및 분석"""
        self.logger.info(
            f"=== Java Log Monitor 시작 (interval={self.interval}s) ==="
        )

        # 시그널 핸들러
        def handle_signal(signum, frame):
            self.logger.info(f"시그널 {signum} 수신, 종료 중...")
            self.running = False

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        cycle_count = 0
        cleanup_interval = 3600  # 1시간마다 데이터 정리
        last_cleanup = time.time()

        while self.running:
            cycle_start = time.time()
            cycle_count += 1

            try:
                window_counts = self.scan_once()
                if window_counts:
                    self.analyze_and_alert(window_counts)
                    total = sum(window_counts.values())
                    self.logger.info(
                        f"[Cycle #{cycle_count}] "
                        f"Exception {len(window_counts)}종 / 총 {total}건"
                    )
                else:
                    self.logger.debug(f"[Cycle #{cycle_count}] 새 Exception 없음")

                # 주기적 데이터 정리
                if time.time() - last_cleanup > cleanup_interval:
                    self.state.cleanup_old_data()
                    last_cleanup = time.time()

            except Exception as e:
                self.logger.error(f"스캔 오류: {e}", exc_info=True)

            # 다음 주기까지 대기
            elapsed = time.time() - cycle_start
            sleep_time = max(0, self.interval - elapsed)
            if sleep_time > 0 and self.running:
                time.sleep(sleep_time)

        self.state.close()
        self.logger.info("=== Java Log Monitor 종료 ===")

    def run_once(self):
        """1회 실행 모드 (cron 연동)"""
        self.logger.info("=== 1회 분석 실행 ===")
        window_counts = self.scan_once()
        if window_counts:
            self.analyze_and_alert(window_counts)
            total = sum(window_counts.values())
            self.logger.info(
                f"결과: Exception {len(window_counts)}종 / 총 {total}건"
            )
        else:
            self.logger.info("새 Exception 없음")
        self.state.close()

    def print_report(self, hours: int = 24):
        """통계 리포트 출력"""
        print(f"\n{'='*60}")
        print(f"  Java Log Trend Report (최근 {hours}시간)")
        print(f"  생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")

        # Top Exception
        top = self.state.get_top_exceptions(hours=hours, limit=15)
        if not top:
            print("  데이터 없음\n")
            self.state.close()
            return

        print(f"  {'Exception Type':<45} {'건수':>8} {'최대스코어':>10}")
        print(f"  {'-'*45} {'-'*8} {'-'*10}")
        for item in top:
            score_bar = '█' * int(item['max_score'] * 10)
            print(
                f"  {item['exception_type']:<45} "
                f"{item['total_count']:>8} "
                f"{item['max_score']:>8.3f}  {score_bar}"
            )

        print(f"\n  총 {len(top)}종 Exception 발견")
        print(f"{'='*60}\n")
        self.state.close()

    def test_config(self):
        """설정 검증 및 테스트 알림 발송"""
        print("=== 설정 검증 ===\n")

        # 로그 파일 확인
        log_files = self._get_log_files()
        print(f"모니터링 대상 파일: {len(log_files)}개")
        for f in log_files:
            size = os.path.getsize(f) if os.path.exists(f) else 0
            print(f"  - {f} ({size:,} bytes)")

        # 알림 채널 확인
        alert_cfg = self.config.get('alerting', {})
        print(f"\n알림 채널:")
        print(f"  - Email: {'활성' if alert_cfg.get('email', {}).get('enabled') else '비활성'}")
        print(f"  - SMS:   {'활성' if alert_cfg.get('sms', {}).get('enabled') else '비활성'}")
        print(f"  - Webhook: {'활성' if alert_cfg.get('webhook', {}).get('enabled') else '비활성'}")

        # 알고리즘 확인
        algo_cfg = self.config.get('algorithms', {})
        print(f"\n알고리즘:")
        for name in ['ewma', 'zscore', 'roc']:
            cfg = algo_cfg.get(name, {})
            status = '활성' if cfg.get('enabled', True) else '비활성'
            weight = cfg.get('weight_in_score', 0)
            print(f"  - {name.upper()}: {status} (비중 {weight:.0%})")

        # 스코어링 설정
        scoring_cfg = self.config.get('scoring', {})
        print(f"\n스코어링:")
        print(f"  - 알림 임계값: {scoring_cfg.get('alert_threshold', 0.6)}")
        print(f"  - 긴급 임계값: {scoring_cfg.get('critical_threshold', 0.85)}")
        print(f"  - 연속 필요 횟수: {scoring_cfg.get('consecutive_anomaly_count', 2)}")

        self.state.close()
        print("\n=== 설정 검증 완료 ===")


def main():
    parser = argparse.ArgumentParser(
        description='Java Log Trend Monitor - Exception 추이 분석 및 이상 탐지'
    )
    parser.add_argument(
        '--config', '-c',
        default='config.yaml',
        help='설정 파일 경로 (기본: config.yaml)'
    )
    parser.add_argument(
        '--mode', '-m',
        choices=['daemon', 'once', 'report', 'test'],
        default='daemon',
        help='실행 모드 (기본: daemon)'
    )
    parser.add_argument(
        '--hours',
        type=int,
        default=24,
        help='리포트 조회 기간 (시간, 기본: 24)'
    )

    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"설정 파일을 찾을 수 없습니다: {args.config}", file=sys.stderr)
        sys.exit(1)

    monitor = LogMonitor(args.config)

    if args.mode == 'daemon':
        monitor.run_daemon()
    elif args.mode == 'once':
        monitor.run_once()
    elif args.mode == 'report':
        monitor.print_report(hours=args.hours)
    elif args.mode == 'test':
        monitor.test_config()


if __name__ == '__main__':
    main()
