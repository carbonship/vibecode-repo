"""
Trend Analyzer - 경량 이상 탐지 알고리즘 모듈
3종 알고리즘 복합 적용:
  1) EWMA (지수가중이동평균) - 최근 추세 반영, 점진적 증가 감지
  2) Z-Score - 통계적 이상치 탐지, 정상 범위 벗어남 감지
  3) ROC (Rate of Change) - 급격한 변화율 탐지, 스파이크 감지

모든 알고리즘은 O(1) 메모리로 증분 계산 가능하도록 설계
"""

import math
from dataclasses import dataclass, field
from collections import deque


@dataclass
class AlgorithmResult:
    """개별 알고리즘의 분석 결과"""
    name: str
    is_anomaly: bool
    score: float          # 0.0 ~ 1.0 정규화된 이상 점수
    current_value: float
    expected_value: float
    detail: str


class EWMADetector:
    """
    지수가중이동평균(EWMA) 기반 이상 탐지

    원리: 최근 데이터에 더 큰 가중치를 부여하여 이동평균 계산.
    현재 값이 EWMA 대비 threshold_multiplier배를 초과하면 이상으로 판정.

    장점: O(1) 메모리, 계산 빠름, 최근 추세 민감
    적합: 점진적 증가 추세, 서서히 악화되는 상황
    """

    def __init__(self, alpha: float = 0.3, threshold_multiplier: float = 2.0):
        self.alpha = alpha
        self.threshold_multiplier = threshold_multiplier
        self.ewma: float | None = None
        self.ewma_variance: float | None = None  # EWMA 분산 (동적 임계값용)

    def update(self, value: float) -> AlgorithmResult:
        if self.ewma is None:
            self.ewma = value
            self.ewma_variance = 0.0
            return AlgorithmResult(
                name="EWMA",
                is_anomaly=False,
                score=0.0,
                current_value=value,
                expected_value=value,
                detail="초기화 중 (첫 번째 데이터 포인트)"
            )

        # EWMA 업데이트: S_t = α * x_t + (1 - α) * S_{t-1}
        prev_ewma = self.ewma
        self.ewma = self.alpha * value + (1 - self.alpha) * self.ewma

        # EWMA 분산 업데이트 (동적 임계값 계산용)
        diff = value - prev_ewma
        self.ewma_variance = (1 - self.alpha) * (self.ewma_variance + self.alpha * diff * diff)

        # 임계값: EWMA + multiplier * sqrt(variance)
        ewma_std = math.sqrt(max(self.ewma_variance, 1e-10))
        threshold = prev_ewma + self.threshold_multiplier * max(ewma_std, prev_ewma * 0.1 + 1)

        is_anomaly = value > threshold and prev_ewma > 0
        # 스코어 계산: 임계값 초과 비율을 0~1로 정규화
        if prev_ewma > 0 and threshold > prev_ewma:
            excess_ratio = max(0, (value - prev_ewma)) / (threshold - prev_ewma)
            score = min(1.0, excess_ratio / 2.0)  # 2배 초과 = 1.0
        else:
            score = 0.0

        return AlgorithmResult(
            name="EWMA",
            is_anomaly=is_anomaly,
            score=score,
            current_value=value,
            expected_value=prev_ewma,
            detail=f"EWMA={prev_ewma:.2f}, 임계값={threshold:.2f}, 분산={self.ewma_variance:.2f}"
        )

    def get_state(self) -> dict:
        return {'ewma': self.ewma, 'ewma_variance': self.ewma_variance}

    def load_state(self, state: dict):
        self.ewma = state.get('ewma')
        self.ewma_variance = state.get('ewma_variance')


class ZScoreDetector:
    """
    Z-Score 기반 이상 탐지

    원리: 이동 윈도우 내 평균과 표준편차를 계산하고,
    현재 값의 Z-Score(표준 점수)가 임계값을 초과하면 이상으로 판정.

    Welford 알고리즘으로 증분 계산하여 수치 안정성 확보.

    장점: 통계적 근거 명확, 수치 안정적
    적합: 정상 범위를 명확히 벗어나는 이상치 감지
    """

    def __init__(self, threshold: float = 2.5, window_size: int = 60):
        self.threshold = threshold
        self.window_size = window_size
        self.values: deque[float] = deque(maxlen=window_size)
        # Welford 증분 통계량
        self.count: int = 0
        self.mean: float = 0.0
        self.m2: float = 0.0  # sum of squares of differences from mean

    def update(self, value: float) -> AlgorithmResult:
        # 윈도우가 가득 차면 가장 오래된 값 제거 (Welford 역연산)
        if len(self.values) == self.window_size:
            old_value = self.values[0]
            self._remove_value(old_value)

        self.values.append(value)
        self._add_value(value)

        if self.count < 2:
            return AlgorithmResult(
                name="Z-Score",
                is_anomaly=False,
                score=0.0,
                current_value=value,
                expected_value=self.mean,
                detail=f"데이터 수집 중 ({self.count}/{self.window_size})"
            )

        std = math.sqrt(self.m2 / self.count)
        if std < 1e-10:
            z_score = 0.0
        else:
            z_score = (value - self.mean) / std

        is_anomaly = z_score > self.threshold
        # 스코어: Z-Score를 0~1 범위로 정규화
        score = min(1.0, max(0.0, z_score / (self.threshold * 2)))

        return AlgorithmResult(
            name="Z-Score",
            is_anomaly=is_anomaly,
            score=score,
            current_value=value,
            expected_value=self.mean,
            detail=f"Z={z_score:.2f}, 평균={self.mean:.2f}, 표준편차={std:.2f}"
        )

    def _add_value(self, value: float):
        """Welford 알고리즘: 값 추가"""
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        delta2 = value - self.mean
        self.m2 += delta * delta2

    def _remove_value(self, value: float):
        """Welford 알고리즘: 값 제거 (슬라이딩 윈도우용)"""
        if self.count <= 1:
            self.count = 0
            self.mean = 0.0
            self.m2 = 0.0
            return
        delta = value - self.mean
        self.count -= 1
        self.mean -= delta / self.count
        delta2 = value - self.mean
        self.m2 -= delta * delta2
        self.m2 = max(0.0, self.m2)  # 부동소수점 오차 보정

    def get_state(self) -> dict:
        return {
            'values': list(self.values),
            'count': self.count,
            'mean': self.mean,
            'm2': self.m2,
        }

    def load_state(self, state: dict):
        self.values = deque(state.get('values', []), maxlen=self.window_size)
        self.count = state.get('count', 0)
        self.mean = state.get('mean', 0.0)
        self.m2 = state.get('m2', 0.0)


class RateOfChangeDetector:
    """
    변화율(ROC) 기반 이상 탐지

    원리: 이전 윈도우 대비 현재 윈도우의 변화율(%)을 계산.
    급격한 증가가 임계값을 초과하면 이상으로 판정.

    장점: 직관적, 스파이크 즉시 감지, O(1) 계산
    적합: 갑자기 Exception이 폭증하는 상황 (장애 발생 등)
    """

    def __init__(self, threshold_percent: float = 200.0):
        self.threshold_percent = threshold_percent
        self.previous_value: float | None = None

    def update(self, value: float) -> AlgorithmResult:
        if self.previous_value is None:
            self.previous_value = value
            return AlgorithmResult(
                name="ROC",
                is_anomaly=False,
                score=0.0,
                current_value=value,
                expected_value=0.0,
                detail="초기화 중 (첫 번째 데이터 포인트)"
            )

        # 변화율 계산 (0 나누기 방지)
        base = max(self.previous_value, 1.0)
        roc_percent = ((value - self.previous_value) / base) * 100.0

        self.previous_value = value

        is_anomaly = roc_percent > self.threshold_percent
        # 스코어: 변화율을 0~1 범위로 정규화
        score = min(1.0, max(0.0, roc_percent / (self.threshold_percent * 2)))

        return AlgorithmResult(
            name="ROC",
            is_anomaly=is_anomaly,
            score=score,
            current_value=value,
            expected_value=base,
            detail=f"변화율={roc_percent:.1f}%, 임계값={self.threshold_percent}%"
        )

    def get_state(self) -> dict:
        return {'previous_value': self.previous_value}

    def load_state(self, state: dict):
        self.previous_value = state.get('previous_value')


class TrendAnalyzer:
    """
    3종 알고리즘 복합 분석기

    각 알고리즘을 독립적으로 실행하고, 결과를 종합하여
    복합 이상 점수를 산출.
    """

    def __init__(self, config: dict):
        algo_config = config.get('algorithms', {})

        self.detectors: list[tuple[str, object, float]] = []  # (name, detector, weight)

        # EWMA
        ewma_cfg = algo_config.get('ewma', {})
        if ewma_cfg.get('enabled', True):
            self.detectors.append((
                'ewma',
                EWMADetector(
                    alpha=ewma_cfg.get('alpha', 0.3),
                    threshold_multiplier=ewma_cfg.get('threshold_multiplier', 2.0),
                ),
                ewma_cfg.get('weight_in_score', 0.35),
            ))

        # Z-Score
        zscore_cfg = algo_config.get('zscore', {})
        if zscore_cfg.get('enabled', True):
            baseline_window = config.get('analysis', {}).get('baseline_window_count', 60)
            self.detectors.append((
                'zscore',
                ZScoreDetector(
                    threshold=zscore_cfg.get('threshold', 2.5),
                    window_size=baseline_window,
                ),
                zscore_cfg.get('weight_in_score', 0.35),
            ))

        # ROC
        roc_cfg = algo_config.get('roc', {})
        if roc_cfg.get('enabled', True):
            self.detectors.append((
                'roc',
                RateOfChangeDetector(
                    threshold_percent=roc_cfg.get('threshold_percent', 200.0),
                ),
                roc_cfg.get('weight_in_score', 0.30),
            ))

    def analyze(self, value: float) -> tuple[list[AlgorithmResult], float]:
        """
        값을 모든 알고리즘에 입력하고 복합 점수 반환.

        Returns:
            (개별 결과 리스트, 가중 합산 점수 0.0~1.0)
        """
        results = []
        weighted_score = 0.0
        total_weight = 0.0

        for name, detector, weight in self.detectors:
            result = detector.update(value)
            results.append(result)
            weighted_score += result.score * weight
            total_weight += weight

        composite_score = weighted_score / total_weight if total_weight > 0 else 0.0
        return results, composite_score

    def get_state(self) -> dict:
        state = {}
        for name, detector, _ in self.detectors:
            state[name] = detector.get_state()
        return state

    def load_state(self, state: dict):
        for name, detector, _ in self.detectors:
            if name in state:
                detector.load_state(state[name])
