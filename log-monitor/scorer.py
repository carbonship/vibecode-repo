"""
Composite Scorer - 가중치 기반 복합 스코어링 시스템

Exception 유형별 가중치 × 알고리즘 이상 점수를 결합하여
최종 중요도 스코어를 산출.

스코어 계산 흐름:
  1) Exception 유형별 기본 가중치 (config에서 설정)
  2) 알고리즘별 이상 점수 (trend_analyzer에서 산출)
  3) 시간대별 가중치 (업무 시간 vs 비업무 시간)
  4) 연속 이상 횟수 부스트 (반복 발생 시 가중)
  → 최종 스코어 = 종합하여 0.0 ~ 1.0 범위로 정규화
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ScoringResult:
    """스코어링 최종 결과"""
    exception_type: str
    final_score: float          # 0.0 ~ 1.0
    severity_level: str         # CRITICAL, HIGH, MEDIUM, LOW, NORMAL
    exception_weight: float     # Exception 유형 가중치
    algorithm_score: float      # 알고리즘 복합 점수
    time_weight: float          # 시간대 가중치
    consecutive_boost: float    # 연속 이상 부스트
    count_in_window: int        # 현재 윈도우의 발생 횟수
    detail: str


class ExceptionWeightManager:
    """Exception 유형별 가중치 관리"""

    def __init__(self, weight_config: dict):
        self.weight_map: dict[str, float] = {}
        self.default_weight = weight_config.get('default_weight', 0.3)

        for severity in ['critical', 'high', 'medium', 'low']:
            cfg = weight_config.get(severity, {})
            weight = cfg.get('weight', self.default_weight)
            for pattern in cfg.get('patterns', []):
                self.weight_map[pattern] = weight

    def get_weight(self, exception_type: str) -> float:
        """Exception 타입명으로 가중치 조회. 패턴 매칭(부분 일치)."""
        # 정확히 일치하는 경우
        if exception_type in self.weight_map:
            return self.weight_map[exception_type]

        # 부분 일치 (예: "com.example.CustomNullPointerException" → "NullPointerException")
        for pattern, weight in self.weight_map.items():
            if exception_type.endswith(pattern) or pattern in exception_type:
                return weight

        return self.default_weight


class TimeWeightCalculator:
    """시간대별 가중치 계산 (업무 시간 장애가 더 중요)"""

    def __init__(
        self,
        business_hours: tuple[int, int] = (9, 18),
        business_weight: float = 1.0,
        off_hours_weight: float = 0.8,
    ):
        self.business_start = business_hours[0]
        self.business_end = business_hours[1]
        self.business_weight = business_weight
        self.off_hours_weight = off_hours_weight

    def get_weight(self, dt: Optional[datetime] = None) -> float:
        if dt is None:
            dt = datetime.now()
        hour = dt.hour
        if self.business_start <= hour < self.business_end:
            return self.business_weight
        return self.off_hours_weight


class CompositeScorer:
    """복합 스코어링 엔진"""

    def __init__(self, config: dict):
        scoring_cfg = config.get('scoring', {})
        self.alert_threshold = scoring_cfg.get('alert_threshold', 0.6)
        self.critical_threshold = scoring_cfg.get('critical_threshold', 0.85)
        self.consecutive_required = scoring_cfg.get('consecutive_anomaly_count', 2)

        self.exception_weights = ExceptionWeightManager(
            config.get('exception_weights', {})
        )
        self.time_weight_calc = TimeWeightCalculator()

        # 연속 이상 카운터 (exception_type → count)
        self.consecutive_counters: dict[str, int] = {}

    def score(
        self,
        exception_type: str,
        count_in_window: int,
        algorithm_score: float,
        timestamp: Optional[datetime] = None,
    ) -> ScoringResult:
        """
        복합 스코어 산출

        Args:
            exception_type: Exception 클래스명
            count_in_window: 현재 윈도우 내 발생 횟수
            algorithm_score: 알고리즘 복합 이상 점수 (0~1)
            timestamp: 현재 시각
        """
        # 1) Exception 유형 가중치
        exc_weight = self.exception_weights.get_weight(exception_type)

        # 2) 시간대 가중치
        time_weight = self.time_weight_calc.get_weight(timestamp)

        # 3) 연속 이상 부스트
        if algorithm_score > 0.3:  # 약간이라도 이상 신호가 있으면
            self.consecutive_counters[exception_type] = (
                self.consecutive_counters.get(exception_type, 0) + 1
            )
        else:
            self.consecutive_counters[exception_type] = 0

        consecutive_count = self.consecutive_counters.get(exception_type, 0)
        # 연속 이상 시 부스트: 1.0 → 1.2 → 1.4 → ... (최대 1.5)
        consecutive_boost = min(1.5, 1.0 + consecutive_count * 0.1)

        # 4) 최종 스코어 계산
        # 가중 합산 후 0~1로 클램핑
        raw_score = algorithm_score * exc_weight * time_weight * consecutive_boost
        final_score = min(1.0, max(0.0, raw_score))

        # 5) 심각도 결정
        severity = self._determine_severity(final_score)

        return ScoringResult(
            exception_type=exception_type,
            final_score=final_score,
            severity_level=severity,
            exception_weight=exc_weight,
            algorithm_score=algorithm_score,
            time_weight=time_weight,
            consecutive_boost=consecutive_boost,
            count_in_window=count_in_window,
            detail=(
                f"스코어={final_score:.3f} "
                f"(알고리즘={algorithm_score:.3f} × "
                f"가중치={exc_weight:.2f} × "
                f"시간={time_weight:.2f} × "
                f"연속부스트={consecutive_boost:.2f})"
            ),
        )

    def _determine_severity(self, score: float) -> str:
        if score >= self.critical_threshold:
            return "CRITICAL"
        elif score >= self.alert_threshold:
            return "HIGH"
        elif score >= self.alert_threshold * 0.7:
            return "MEDIUM"
        elif score >= self.alert_threshold * 0.4:
            return "LOW"
        return "NORMAL"

    def should_alert(self, result: ScoringResult) -> bool:
        """알림 발송 여부 판단"""
        # CRITICAL은 즉시 알림
        if result.final_score >= self.critical_threshold:
            return True

        # HIGH 이상이면서 연속 횟수 충족 시 알림
        if result.final_score >= self.alert_threshold:
            consecutive = self.consecutive_counters.get(result.exception_type, 0)
            return consecutive >= self.consecutive_required

        return False

    def reset_consecutive(self, exception_type: str):
        """알림 발송 후 연속 카운터 리셋"""
        self.consecutive_counters[exception_type] = 0
