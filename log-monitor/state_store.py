"""
State Store - SQLite 기반 경량 상태 저장소

역할:
  - 파일 오프셋 추적 (마지막 읽은 위치)
  - 알고리즘 상태 영속화 (재시작 시 복원)
  - 시간별 Exception 통계 히스토리
  - 알림 이력 관리
  - 오래된 데이터 자동 정리 (retention)
"""

import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class StateStore:
    """SQLite 기반 상태 관리"""

    def __init__(self, db_path: str, retention_days: int = 30):
        self.db_path = db_path
        self.retention_days = retention_days
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")  # 동시 읽기 성능 향상
        self.conn.execute("PRAGMA synchronous=NORMAL")  # 쓰기 성능 향상
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS file_offsets (
                filepath TEXT PRIMARY KEY,
                offset INTEGER NOT NULL DEFAULT 0,
                inode INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS algorithm_state (
                key TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS exception_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                exception_type TEXT NOT NULL,
                count INTEGER NOT NULL,
                score REAL NOT NULL,
                severity TEXT NOT NULL,
                source_file TEXT
            );

            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                exception_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                score REAL NOT NULL,
                channels TEXT NOT NULL,
                detail TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_stats_timestamp
                ON exception_stats(timestamp);
            CREATE INDEX IF NOT EXISTS idx_stats_exception
                ON exception_stats(exception_type, timestamp);
            CREATE INDEX IF NOT EXISTS idx_alert_timestamp
                ON alert_history(timestamp);
        """)

    # --- 파일 오프셋 관리 ---

    def get_file_offset(self, filepath: str) -> tuple[int, int]:
        """(offset, inode) 반환"""
        row = self.conn.execute(
            "SELECT offset, inode FROM file_offsets WHERE filepath = ?",
            (filepath,)
        ).fetchone()
        if row:
            return row[0], row[1]
        return 0, 0

    def set_file_offset(self, filepath: str, offset: int, inode: int):
        now = datetime.now().isoformat()
        self.conn.execute(
            """INSERT INTO file_offsets (filepath, offset, inode, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(filepath) DO UPDATE SET
                 offset=excluded.offset,
                 inode=excluded.inode,
                 updated_at=excluded.updated_at""",
            (filepath, offset, inode, now)
        )

    # --- 알고리즘 상태 관리 ---

    def get_algorithm_state(self, key: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT state_json FROM algorithm_state WHERE key = ?",
            (key,)
        ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def set_algorithm_state(self, key: str, state: dict):
        now = datetime.now().isoformat()
        self.conn.execute(
            """INSERT INTO algorithm_state (key, state_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 state_json=excluded.state_json,
                 updated_at=excluded.updated_at""",
            (key, json.dumps(state), now)
        )

    # --- Exception 통계 ---

    def record_exception_stats(
        self,
        exception_type: str,
        count: int,
        score: float,
        severity: str,
        source_file: str = '',
    ):
        now = datetime.now().isoformat()
        self.conn.execute(
            """INSERT INTO exception_stats
               (timestamp, exception_type, count, score, severity, source_file)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (now, exception_type, count, score, severity, source_file)
        )

    def get_recent_stats(
        self, exception_type: str, hours: int = 24
    ) -> list[dict]:
        """최근 N시간의 통계 조회"""
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            """SELECT timestamp, count, score, severity
               FROM exception_stats
               WHERE exception_type = ? AND timestamp >= ?
               ORDER BY timestamp""",
            (exception_type, since)
        ).fetchall()
        return [
            {'timestamp': r[0], 'count': r[1], 'score': r[2], 'severity': r[3]}
            for r in rows
        ]

    def get_top_exceptions(self, hours: int = 24, limit: int = 10) -> list[dict]:
        """최근 N시간 내 가장 많이 발생한 Exception Top N"""
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            """SELECT exception_type, SUM(count) as total, MAX(score) as max_score
               FROM exception_stats
               WHERE timestamp >= ?
               GROUP BY exception_type
               ORDER BY total DESC
               LIMIT ?""",
            (since, limit)
        ).fetchall()
        return [
            {'exception_type': r[0], 'total_count': r[1], 'max_score': r[2]}
            for r in rows
        ]

    # --- 알림 이력 ---

    def record_alert(
        self,
        exception_type: str,
        severity: str,
        score: float,
        channels: str,
        detail: str = '',
    ):
        now = datetime.now().isoformat()
        self.conn.execute(
            """INSERT INTO alert_history
               (timestamp, exception_type, severity, score, channels, detail)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (now, exception_type, severity, score, channels, detail)
        )

    # --- 데이터 정리 ---

    def cleanup_old_data(self):
        """retention_days 이전 데이터 삭제"""
        cutoff = (datetime.now() - timedelta(days=self.retention_days)).isoformat()
        deleted_stats = self.conn.execute(
            "DELETE FROM exception_stats WHERE timestamp < ?", (cutoff,)
        ).rowcount
        deleted_alerts = self.conn.execute(
            "DELETE FROM alert_history WHERE timestamp < ?", (cutoff,)
        ).rowcount

        if deleted_stats or deleted_alerts:
            logger.info(
                f"오래된 데이터 정리: 통계 {deleted_stats}건, "
                f"알림이력 {deleted_alerts}건 삭제"
            )
            self.conn.execute("PRAGMA optimize")

    def close(self):
        if self.conn:
            self.conn.close()
