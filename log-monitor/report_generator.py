#!/usr/bin/env python3
"""
Report Generator - 로그 분석 결과 HTML 시각화 리포트 생성

SQLite 상태 DB에서 데이터를 읽어 Chart.js 기반의
인터랙티브 HTML 리포트를 생성합니다.

사용법:
  python report_generator.py --db /tmp/test_log_monitor_state.db --output report.html --hours 24
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from collections import defaultdict


def query_db(db_path: str, hours: int) -> dict:
    """SQLite DB에서 리포트에 필요한 모든 데이터 조회"""
    conn = sqlite3.connect(db_path)
    since = (datetime.now() - timedelta(hours=hours)).isoformat()

    data = {}

    # 1) Exception 유형별 집계
    rows = conn.execute("""
        SELECT exception_type, SUM(count) as total, MAX(score) as max_score,
               AVG(score) as avg_score, COUNT(*) as occurrences
        FROM exception_stats
        WHERE timestamp >= ?
        GROUP BY exception_type
        ORDER BY total DESC
    """, (since,)).fetchall()
    data['top_exceptions'] = [
        {'type': r[0], 'total': r[1], 'max_score': r[2],
         'avg_score': r[3], 'occurrences': r[4]}
        for r in rows
    ]

    # 2) 시계열 데이터 (전체 Exception 건수 추이)
    rows = conn.execute("""
        SELECT timestamp, SUM(count) as total, MAX(score) as max_score
        FROM exception_stats
        WHERE timestamp >= ?
        GROUP BY timestamp
        ORDER BY timestamp
    """, (since,)).fetchall()
    data['timeline'] = [
        {'timestamp': r[0], 'count': r[1], 'max_score': r[2]}
        for r in rows
    ]

    # 3) Exception 유형별 시계열 (상위 5개)
    top5_types = [e['type'] for e in data['top_exceptions'][:5]]
    data['timeline_by_type'] = {}
    for exc_type in top5_types:
        rows = conn.execute("""
            SELECT timestamp, count, score
            FROM exception_stats
            WHERE exception_type = ? AND timestamp >= ?
            ORDER BY timestamp
        """, (exc_type, since)).fetchall()
        data['timeline_by_type'][exc_type] = [
            {'timestamp': r[0], 'count': r[1], 'score': r[2]}
            for r in rows
        ]

    # 4) 심각도별 분포
    rows = conn.execute("""
        SELECT severity, COUNT(*) as cnt, SUM(count) as total
        FROM exception_stats
        WHERE timestamp >= ?
        GROUP BY severity
        ORDER BY
            CASE severity
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'MEDIUM' THEN 3
                WHEN 'LOW' THEN 4
                ELSE 5
            END
    """, (since,)).fetchall()
    data['severity_dist'] = [
        {'severity': r[0], 'count': r[1], 'total_exceptions': r[2]}
        for r in rows
    ]

    # 5) 스코어 분포 (히스토그램용)
    rows = conn.execute("""
        SELECT score FROM exception_stats WHERE timestamp >= ?
    """, (since,)).fetchall()
    scores = [r[0] for r in rows]
    bins = [0] * 10  # 0.0-0.1, 0.1-0.2, ..., 0.9-1.0
    for s in scores:
        idx = min(int(s * 10), 9)
        bins[idx] += 1
    data['score_histogram'] = bins

    # 6) 알림 이력
    rows = conn.execute("""
        SELECT timestamp, exception_type, severity, score, detail
        FROM alert_history
        WHERE timestamp >= ?
        ORDER BY timestamp DESC
        LIMIT 20
    """, (since,)).fetchall()
    data['alert_history'] = [
        {'timestamp': r[0], 'type': r[1], 'severity': r[2],
         'score': r[3], 'detail': r[4] or ''}
        for r in rows
    ]

    # 7) 요약 통계
    summary = conn.execute("""
        SELECT COUNT(*), SUM(count), MAX(score), AVG(score)
        FROM exception_stats WHERE timestamp >= ?
    """, (since,)).fetchone()
    data['summary'] = {
        'analysis_cycles': summary[0] or 0,
        'total_exceptions': summary[1] or 0,
        'max_score': summary[2] or 0,
        'avg_score': summary[3] or 0,
        'unique_types': len(data['top_exceptions']),
        'period_hours': hours,
    }

    conn.close()
    return data


def generate_html(data: dict) -> str:
    """데이터를 기반으로 HTML 리포트 생성"""

    # 타임라인 데이터 가공
    timeline_labels = json.dumps([
        d['timestamp'].split('T')[-1][:8] if 'T' in d['timestamp']
        else d['timestamp'].split(' ')[-1][:8]
        for d in data['timeline']
    ])
    timeline_counts = json.dumps([d['count'] for d in data['timeline']])
    timeline_scores = json.dumps([round(d['max_score'], 3) for d in data['timeline']])

    # Top Exception 데이터
    top_exc_labels = json.dumps([e['type'].split('.')[-1][:25] for e in data['top_exceptions'][:10]])
    top_exc_counts = json.dumps([e['total'] for e in data['top_exceptions'][:10]])
    top_exc_scores = json.dumps([round(e['max_score'], 3) for e in data['top_exceptions'][:10]])

    # 심각도 분포
    severity_labels = json.dumps([d['severity'] for d in data['severity_dist']])
    severity_counts = json.dumps([d['total_exceptions'] for d in data['severity_dist']])

    # 스코어 히스토그램
    score_hist = json.dumps(data['score_histogram'])

    # Exception 유형별 시계열 (상위 5개) - 각각의 데이터셋
    type_timeline_datasets = []
    colors = ['#e53935', '#fb8c00', '#fdd835', '#43a047', '#1e88e5',
              '#8e24aa', '#00acc1', '#6d4c41', '#546e7a', '#d81b60']
    for i, (exc_type, series) in enumerate(data['timeline_by_type'].items()):
        short_name = exc_type.split('.')[-1][:20]
        color = colors[i % len(colors)]
        type_timeline_datasets.append({
            'label': short_name,
            'data': [d['count'] for d in series],
            'borderColor': color,
            'backgroundColor': color + '20',
            'tension': 0.3,
            'fill': False,
        })
    type_timeline_labels = []
    if data['timeline_by_type']:
        first_series = list(data['timeline_by_type'].values())[0]
        type_timeline_labels = [
            d['timestamp'].split('T')[-1][:8] if 'T' in d['timestamp']
            else d['timestamp'].split(' ')[-1][:8]
            for d in first_series
        ]

    # 요약
    s = data['summary']

    # 상세 테이블 행
    detail_rows = ''
    for e in data['top_exceptions']:
        score = e['max_score']
        if score >= 0.85:
            badge = '<span class="badge critical">CRITICAL</span>'
        elif score >= 0.6:
            badge = '<span class="badge high">HIGH</span>'
        elif score >= 0.42:
            badge = '<span class="badge medium">MEDIUM</span>'
        elif score >= 0.24:
            badge = '<span class="badge low">LOW</span>'
        else:
            badge = '<span class="badge normal">NORMAL</span>'

        bar_width = min(100, int(score * 100))
        bar_color = '#e53935' if score >= 0.6 else '#fb8c00' if score >= 0.4 else '#43a047'
        detail_rows += f"""
            <tr>
                <td class="exc-name">{e['type']}</td>
                <td class="num">{e['total']:,}</td>
                <td class="num">{e['occurrences']}</td>
                <td class="num">{e['avg_score']:.3f}</td>
                <td>
                    <div class="score-cell">
                        <div class="score-bar" style="width:{bar_width}%;background:{bar_color}"></div>
                        <span>{score:.3f}</span>
                    </div>
                </td>
                <td>{badge}</td>
            </tr>"""

    # 알림 이력 행
    alert_rows = ''
    if data['alert_history']:
        for a in data['alert_history']:
            ts = a['timestamp'][:19].replace('T', ' ')
            alert_rows += f"""
                <tr>
                    <td>{ts}</td>
                    <td><span class="badge {a['severity'].lower()}">{a['severity']}</span></td>
                    <td>{a['type']}</td>
                    <td class="num">{a['score']:.3f}</td>
                </tr>"""
    else:
        alert_rows = '<tr><td colspan="4" class="empty">알림 이력 없음 (알림 채널 비활성화 상태)</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Java Log Trend Analysis Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e0e0e0;
    --text-dim: #888;
    --accent: #4fc3f7;
    --red: #e53935;
    --orange: #fb8c00;
    --yellow: #fdd835;
    --green: #43a047;
    --blue: #1e88e5;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 20px;
    line-height: 1.6;
  }}
  .header {{
    text-align: center;
    padding: 30px 0 20px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 24px;
  }}
  .header h1 {{
    font-size: 28px;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: -0.5px;
  }}
  .header .subtitle {{
    color: var(--text-dim);
    font-size: 14px;
    margin-top: 6px;
  }}
  .summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }}
  .summary-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    text-align: center;
  }}
  .summary-card .label {{
    font-size: 12px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 1px;
  }}
  .summary-card .value {{
    font-size: 32px;
    font-weight: 700;
    margin: 8px 0 0;
  }}
  .summary-card .value.red {{ color: var(--red); }}
  .summary-card .value.orange {{ color: var(--orange); }}
  .summary-card .value.blue {{ color: var(--accent); }}
  .summary-card .value.green {{ color: var(--green); }}

  .charts-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
    gap: 20px;
    margin-bottom: 24px;
  }}
  .chart-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
  }}
  .chart-card h3 {{
    font-size: 16px;
    margin-bottom: 16px;
    color: var(--text);
    font-weight: 600;
  }}
  .chart-card canvas {{
    max-height: 320px;
  }}
  .full-width {{ grid-column: 1 / -1; }}

  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  th {{
    text-align: left;
    padding: 10px 12px;
    background: #12141c;
    color: var(--text-dim);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 2px solid var(--border);
  }}
  td {{
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
  }}
  tr:hover {{ background: #1f2233; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .exc-name {{
    font-family: 'Consolas', 'Fira Code', monospace;
    font-size: 12px;
    color: var(--accent);
    max-width: 360px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .empty {{ text-align: center; color: var(--text-dim); padding: 24px; }}

  .badge {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
  }}
  .badge.critical {{ background: var(--red); color: #fff; }}
  .badge.high {{ background: var(--orange); color: #fff; }}
  .badge.medium {{ background: var(--yellow); color: #000; }}
  .badge.low {{ background: var(--green); color: #fff; }}
  .badge.normal {{ background: var(--border); color: var(--text-dim); }}

  .score-cell {{
    position: relative;
    width: 120px;
    height: 22px;
    background: #12141c;
    border-radius: 4px;
    overflow: hidden;
  }}
  .score-bar {{
    position: absolute;
    top: 0; left: 0;
    height: 100%;
    border-radius: 4px;
    opacity: 0.7;
    transition: width 0.3s;
  }}
  .score-cell span {{
    position: relative;
    z-index: 1;
    display: block;
    text-align: center;
    font-size: 12px;
    font-weight: 600;
    line-height: 22px;
  }}
  .footer {{
    text-align: center;
    padding: 20px;
    color: var(--text-dim);
    font-size: 12px;
    border-top: 1px solid var(--border);
    margin-top: 24px;
  }}
  @media (max-width: 768px) {{
    .charts-grid {{ grid-template-columns: 1fr; }}
    body {{ padding: 12px; }}
  }}
</style>
</head>
<body>

<div class="header">
  <h1>Java Log Trend Analysis Report</h1>
  <div class="subtitle">
    생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 조회 기간: 최근 {s['period_hours']}시간
  </div>
</div>

<!-- 요약 카드 -->
<div class="summary-grid">
  <div class="summary-card">
    <div class="label">분석 주기</div>
    <div class="value blue">{s['analysis_cycles']}</div>
  </div>
  <div class="summary-card">
    <div class="label">총 Exception 건수</div>
    <div class="value orange">{s['total_exceptions']:,}</div>
  </div>
  <div class="summary-card">
    <div class="label">Exception 유형</div>
    <div class="value blue">{s['unique_types']}</div>
  </div>
  <div class="summary-card">
    <div class="label">최대 스코어</div>
    <div class="value {'red' if s['max_score'] >= 0.6 else 'orange' if s['max_score'] >= 0.3 else 'green'}">{s['max_score']:.3f}</div>
  </div>
  <div class="summary-card">
    <div class="label">평균 스코어</div>
    <div class="value green">{s['avg_score']:.3f}</div>
  </div>
</div>

<!-- 차트 영역 -->
<div class="charts-grid">
  <div class="chart-card full-width">
    <h3>Exception 발생 건수 추이 (시간순)</h3>
    <canvas id="timelineChart"></canvas>
  </div>

  <div class="chart-card full-width">
    <h3>이상 스코어 추이</h3>
    <canvas id="scoreTimelineChart"></canvas>
  </div>

  <div class="chart-card full-width">
    <h3>Exception 유형별 추이 (Top 5)</h3>
    <canvas id="typeTimelineChart"></canvas>
  </div>

  <div class="chart-card">
    <h3>Top 10 Exception (발생 건수)</h3>
    <canvas id="topExcChart"></canvas>
  </div>

  <div class="chart-card">
    <h3>심각도 분포</h3>
    <canvas id="severityChart"></canvas>
  </div>

  <div class="chart-card">
    <h3>스코어 분포 (히스토그램)</h3>
    <canvas id="scoreHistChart"></canvas>
  </div>

  <div class="chart-card">
    <h3>Top 10 Exception (최대 스코어)</h3>
    <canvas id="topScoreChart"></canvas>
  </div>
</div>

<!-- Exception 상세 테이블 -->
<div class="chart-card full-width" style="margin-bottom:24px">
  <h3>Exception 상세</h3>
  <table>
    <thead>
      <tr>
        <th>Exception Type</th>
        <th style="text-align:right">총 건수</th>
        <th style="text-align:right">탐지 횟수</th>
        <th style="text-align:right">평균 스코어</th>
        <th>최대 스코어</th>
        <th>심각도</th>
      </tr>
    </thead>
    <tbody>{detail_rows}</tbody>
  </table>
</div>

<!-- 알림 이력 -->
<div class="chart-card full-width">
  <h3>알림 발송 이력</h3>
  <table>
    <thead>
      <tr>
        <th>시각</th>
        <th>심각도</th>
        <th>Exception Type</th>
        <th style="text-align:right">스코어</th>
      </tr>
    </thead>
    <tbody>{alert_rows}</tbody>
  </table>
</div>

<div class="footer">
  Java Log Trend Monitor &mdash; Generated by report_generator.py
</div>

<script>
Chart.defaults.color = '#888';
Chart.defaults.borderColor = '#2a2d3a';
const gridColor = '#1f2233';

// 1) Exception 건수 추이
new Chart(document.getElementById('timelineChart'), {{
  type: 'bar',
  data: {{
    labels: {timeline_labels},
    datasets: [{{
      label: 'Exception 건수',
      data: {timeline_counts},
      backgroundColor: '#4fc3f740',
      borderColor: '#4fc3f7',
      borderWidth: 1,
      borderRadius: 3,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: gridColor }} }},
      y: {{ beginAtZero: true, grid: {{ color: gridColor }} }}
    }}
  }}
}});

// 2) 스코어 추이
new Chart(document.getElementById('scoreTimelineChart'), {{
  type: 'line',
  data: {{
    labels: {timeline_labels},
    datasets: [
      {{
        label: '최대 스코어',
        data: {timeline_scores},
        borderColor: '#e53935',
        backgroundColor: '#e5393520',
        tension: 0.3,
        fill: true,
        pointRadius: 3,
      }},
      {{
        label: '알림 임계값 (0.5)',
        data: Array({len(data['timeline'])}).fill(0.5),
        borderColor: '#fb8c0080',
        borderDash: [6, 4],
        pointRadius: 0,
        fill: false,
      }},
      {{
        label: '긴급 임계값 (0.8)',
        data: Array({len(data['timeline'])}).fill(0.8),
        borderColor: '#e5393580',
        borderDash: [6, 4],
        pointRadius: 0,
        fill: false,
      }}
    ]
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ grid: {{ color: gridColor }} }},
      y: {{ min: 0, max: 1, grid: {{ color: gridColor }} }}
    }}
  }}
}});

// 3) Exception 유형별 추이
new Chart(document.getElementById('typeTimelineChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(type_timeline_labels)},
    datasets: {json.dumps(type_timeline_datasets)},
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ grid: {{ color: gridColor }} }},
      y: {{ beginAtZero: true, grid: {{ color: gridColor }} }}
    }}
  }}
}});

// 4) Top Exception 바 차트
new Chart(document.getElementById('topExcChart'), {{
  type: 'bar',
  data: {{
    labels: {top_exc_labels},
    datasets: [{{
      label: '건수',
      data: {top_exc_counts},
      backgroundColor: ['#e53935','#fb8c00','#fdd835','#43a047','#1e88e5',
                         '#8e24aa','#00acc1','#6d4c41','#546e7a','#d81b60'],
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    indexAxis: 'y',
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ beginAtZero: true, grid: {{ color: gridColor }} }},
      y: {{ grid: {{ color: gridColor }} }}
    }}
  }}
}});

// 5) 심각도 도넛
new Chart(document.getElementById('severityChart'), {{
  type: 'doughnut',
  data: {{
    labels: {severity_labels},
    datasets: [{{
      data: {severity_counts},
      backgroundColor: {{
        'CRITICAL': '#e53935', 'HIGH': '#fb8c00', 'MEDIUM': '#fdd835',
        'LOW': '#43a047', 'NORMAL': '#546e7a'
      }}[undefined] || ['#e53935','#fb8c00','#fdd835','#43a047','#546e7a'],
      borderWidth: 0,
    }}]
  }},
  options: {{
    responsive: true,
    cutout: '55%',
    plugins: {{
      legend: {{ position: 'bottom' }}
    }}
  }}
}});

// 6) 스코어 히스토그램
new Chart(document.getElementById('scoreHistChart'), {{
  type: 'bar',
  data: {{
    labels: ['0.0-0.1','0.1-0.2','0.2-0.3','0.3-0.4','0.4-0.5',
             '0.5-0.6','0.6-0.7','0.7-0.8','0.8-0.9','0.9-1.0'],
    datasets: [{{
      label: '빈도',
      data: {score_hist},
      backgroundColor: [
        '#43a047','#43a047','#43a047','#fdd835','#fdd835',
        '#fb8c00','#fb8c00','#e53935','#e53935','#e53935'
      ],
      borderRadius: 3,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: gridColor }} }},
      y: {{ beginAtZero: true, grid: {{ color: gridColor }} }}
    }}
  }}
}});

// 7) Top Exception 스코어
new Chart(document.getElementById('topScoreChart'), {{
  type: 'bar',
  data: {{
    labels: {top_exc_labels},
    datasets: [{{
      label: '최대 스코어',
      data: {top_exc_scores},
      backgroundColor: {top_exc_scores}.map(v =>
        v >= 0.6 ? '#e53935' : v >= 0.4 ? '#fb8c00' : v >= 0.2 ? '#fdd835' : '#43a047'
      ),
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    indexAxis: 'y',
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ min: 0, max: 1, grid: {{ color: gridColor }} }},
      y: {{ grid: {{ color: gridColor }} }}
    }}
  }}
}});
</script>

</body>
</html>"""

    return html


def main():
    parser = argparse.ArgumentParser(
        description='Java Log Trend Analysis - HTML 시각화 리포트 생성'
    )
    parser.add_argument(
        '--db', required=True,
        help='SQLite 상태 DB 파일 경로'
    )
    parser.add_argument(
        '--output', '-o', default='report.html',
        help='출력 HTML 파일 (기본: report.html)'
    )
    parser.add_argument(
        '--hours', type=int, default=24,
        help='조회 기간 (시간, 기본: 24)'
    )
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"[ERROR] DB 파일을 찾을 수 없습니다: {args.db}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] 데이터 조회 중: {args.db} (최근 {args.hours}시간)")
    data = query_db(args.db, args.hours)

    print(f"[*] HTML 리포트 생성 중...")
    html = generate_html(data)

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(html)

    size = os.path.getsize(args.output)
    print(f"[*] 리포트 생성 완료: {args.output} ({size:,} bytes)")
    print(f"    - 분석 주기: {data['summary']['analysis_cycles']}회")
    print(f"    - 총 Exception: {data['summary']['total_exceptions']:,}건")
    print(f"    - Exception 유형: {data['summary']['unique_types']}종")


if __name__ == '__main__':
    main()
