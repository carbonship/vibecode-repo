"""
Java Log Streaming Parser
- 대용량 로그 파일을 라인 단위 스트리밍으로 처리
- 파일 오프셋 추적으로 중복 읽기 방지
- 멀티라인 스택트레이스 병합
- 로그 로테이션 감지
"""

import os
import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Generator, Optional


@dataclass
class LogEntry:
    timestamp: Optional[datetime]
    level: str
    exception_type: str
    message: str
    stacktrace: str
    source_file: str
    line_number: int


# Java 로그 라인 시작 패턴 (타임스탬프로 시작하는 라인)
LOG_LINE_PATTERN = re.compile(
    r'^(\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}[,.\d]*)\s+'
    r'(\[?(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|SEVERE)\]?)\s+'
    r'(.*)',
    re.DOTALL
)

# Exception 클래스명 추출 패턴
EXCEPTION_PATTERN = re.compile(
    r'([\w$.]+(?:Exception|Error|Throwable|Fault))\b'
)

# "at com.example.Class.method(File.java:123)" 패턴
STACKTRACE_LINE_PATTERN = re.compile(
    r'^\s+at\s+[\w$.<>]+\(.*\)\s*$'
)

# "Caused by:" 패턴
CAUSED_BY_PATTERN = re.compile(
    r'^\s*Caused by:\s+'
)


class JavaLogParser:
    """대용량 Java 로그 파일을 스트리밍 방식으로 파싱"""

    def __init__(self, timestamp_formats: list[str], encoding: str = 'utf-8'):
        self.timestamp_formats = timestamp_formats
        self.encoding = encoding

    def parse_timestamp(self, ts_str: str) -> Optional[datetime]:
        """여러 포맷을 시도하여 타임스탬프 파싱"""
        ts_str = ts_str.strip()
        for fmt in self.timestamp_formats:
            try:
                return datetime.strptime(ts_str, fmt)
            except ValueError:
                continue
        return None

    def is_log_start(self, line: str) -> bool:
        """새 로그 엔트리의 시작 라인인지 판별"""
        return LOG_LINE_PATTERN.match(line) is not None

    def extract_exceptions(self, text: str) -> list[str]:
        """텍스트에서 모든 Exception 클래스명 추출"""
        return EXCEPTION_PATTERN.findall(text)

    def parse_line(self, line: str, source_file: str, line_number: int) -> Optional[dict]:
        """단일 로그 라인의 기본 정보 추출"""
        m = LOG_LINE_PATTERN.match(line)
        if not m:
            return None
        return {
            'timestamp_str': m.group(1),
            'level': m.group(2).strip('[]').upper(),
            'body': m.group(3),
            'source_file': source_file,
            'line_number': line_number,
        }

    def stream_entries(
        self,
        filepath: str,
        start_offset: int = 0
    ) -> Generator[tuple[LogEntry, int], None, None]:
        """
        로그 파일을 스트리밍으로 읽으며 LogEntry를 생성.

        yields: (LogEntry, current_offset) 튜플

        - start_offset부터 읽기 시작 (이전 위치 이어읽기)
        - 멀티라인 스택트레이스를 하나의 엔트리로 병합
        - 메모리에 전체 파일을 올리지 않음
        """
        file_size = os.path.getsize(filepath)

        # 로그 로테이션 감지: 파일이 이전 오프셋보다 작아졌으면 처음부터 읽기
        if start_offset > file_size:
            start_offset = 0

        current_entry = None
        stacktrace_lines = []
        entry_line_number = 0

        with open(filepath, 'r', encoding=self.encoding, errors='replace') as f:
            f.seek(start_offset)
            line_num = 0

            # readline()을 사용하여 tell()과 호환되도록 함
            # (for line in f: 순회 중에는 tell() 사용 불가)
            while True:
                line = f.readline()
                if not line:
                    break

                line_num += 1
                current_offset = f.tell()
                stripped = line.rstrip('\n\r')

                parsed = self.parse_line(stripped, filepath, line_num)

                if parsed:
                    # 이전에 모으던 엔트리가 있으면 방출
                    if current_entry:
                        entry = self._build_entry(
                            current_entry, stacktrace_lines
                        )
                        if entry and entry.exception_type:
                            yield entry, current_offset
                        stacktrace_lines = []

                    current_entry = parsed
                    entry_line_number = line_num
                else:
                    # 스택트레이스 라인이거나 Caused by 라인
                    if current_entry:
                        if (STACKTRACE_LINE_PATTERN.match(stripped)
                                or CAUSED_BY_PATTERN.match(stripped)
                                or stripped.startswith('\t')
                                or stripped.strip().startswith('...')):
                            stacktrace_lines.append(stripped)
                        else:
                            # Exception 메시지의 연속 라인일 수 있음
                            if current_entry.get('body') is not None:
                                current_entry['body'] += ' ' + stripped.strip()

            # 마지막 엔트리 처리
            if current_entry:
                entry = self._build_entry(current_entry, stacktrace_lines)
                if entry and entry.exception_type:
                    yield entry, f.tell()

    def _build_entry(
        self, parsed: dict, stacktrace_lines: list[str]
    ) -> Optional[LogEntry]:
        """파싱된 정보로 LogEntry 생성"""
        full_text = parsed['body'] + '\n' + '\n'.join(stacktrace_lines)
        exceptions = self.extract_exceptions(full_text)

        if not exceptions:
            return None

        # 가장 먼저 나오는 Exception을 주요 Exception으로
        primary_exception = exceptions[0]

        # Caused by에서 추출된 근본 원인이 있으면 그것을 사용
        for line in stacktrace_lines:
            if CAUSED_BY_PATTERN.match(line):
                caused_exceptions = self.extract_exceptions(line)
                if caused_exceptions:
                    primary_exception = caused_exceptions[0]
                    break

        timestamp = self.parse_timestamp(parsed['timestamp_str'])

        return LogEntry(
            timestamp=timestamp,
            level=parsed['level'],
            exception_type=primary_exception,
            message=parsed['body'][:500],  # 메시지 길이 제한
            stacktrace='\n'.join(stacktrace_lines[-20:]),  # 스택트레이스 마지막 20줄
            source_file=parsed['source_file'],
            line_number=parsed['line_number'],
        )

    def get_file_inode(self, filepath: str) -> int:
        """파일의 inode 번호 반환 (로그 로테이션 감지용)"""
        try:
            stat = os.stat(filepath)
            return stat.st_ino
        except OSError:
            return 0
