"""
Alert Notifier - SMS / 이메일 / Webhook 알림 발송 모듈

지원 채널:
  - Email (SMTP)
  - SMS (Twilio API)
  - Webhook (Slack, Teams 등)

환경변수 참조: config에서 ${VAR_NAME} 형식으로 비밀값 참조
"""

import os
import re
import json
import smtplib
import logging
import urllib.request
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional

from scorer import ScoringResult

logger = logging.getLogger(__name__)

# 환경변수 참조 패턴: ${VAR_NAME}
ENV_VAR_PATTERN = re.compile(r'\$\{(\w+)\}')


def resolve_env_vars(value: str) -> str:
    """문자열 내 ${ENV_VAR} 패턴을 환경변수 값으로 치환"""
    if not isinstance(value, str):
        return value

    def replacer(match):
        var_name = match.group(1)
        env_val = os.environ.get(var_name, '')
        if not env_val:
            logger.warning(f"환경변수 {var_name}이(가) 설정되지 않았습니다")
        return env_val

    return ENV_VAR_PATTERN.sub(replacer, value)


class AlertThrottler:
    """알림 쿨다운 관리 - 동일 유형의 알림 폭주 방지"""

    def __init__(self, cooldown_seconds: int = 300):
        self.cooldown_seconds = cooldown_seconds
        self.last_alert_times: dict[str, datetime] = {}

    def can_send(self, alert_key: str) -> bool:
        now = datetime.now()
        last_time = self.last_alert_times.get(alert_key)
        if last_time is None:
            return True
        elapsed = (now - last_time).total_seconds()
        return elapsed >= self.cooldown_seconds

    def record_sent(self, alert_key: str):
        self.last_alert_times[alert_key] = datetime.now()


class EmailNotifier:
    """SMTP 이메일 알림"""

    def __init__(self, config: dict):
        self.enabled = config.get('enabled', False)
        if not self.enabled:
            return
        self.smtp_host = config.get('smtp_host', 'smtp.gmail.com')
        self.smtp_port = config.get('smtp_port', 587)
        self.use_tls = config.get('use_tls', True)
        self.username = resolve_env_vars(config.get('username', ''))
        self.password = resolve_env_vars(config.get('password', ''))
        self.from_address = config.get('from_address', '')
        self.to_addresses = config.get('to_addresses', [])

    def send(self, subject: str, body: str) -> bool:
        if not self.enabled:
            return False
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.from_address
            msg['To'] = ', '.join(self.to_addresses)

            text_part = MIMEText(body, 'plain', 'utf-8')
            html_part = MIMEText(self._to_html(body), 'html', 'utf-8')
            msg.attach(text_part)
            msg.attach(html_part)

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.use_tls:
                    server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.sendmail(self.from_address, self.to_addresses, msg.as_string())

            logger.info(f"이메일 발송 완료: {subject}")
            return True
        except Exception as e:
            logger.error(f"이메일 발송 실패: {e}")
            return False

    def _to_html(self, text: str) -> str:
        lines = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        html_body = lines.replace('\n', '<br>\n')
        return f"""
        <html><body style="font-family: monospace; font-size: 13px;">
        <h2 style="color: #d32f2f;">Java Log Anomaly Alert</h2>
        <div style="background: #f5f5f5; padding: 16px; border-radius: 4px;">
        {html_body}
        </div>
        </body></html>
        """


class SMSNotifier:
    """Twilio SMS 알림"""

    def __init__(self, config: dict):
        self.enabled = config.get('enabled', False)
        if not self.enabled:
            return
        self.account_sid = resolve_env_vars(config.get('account_sid', ''))
        self.auth_token = resolve_env_vars(config.get('auth_token', ''))
        self.from_number = config.get('from_number', '')
        self.to_numbers = config.get('to_numbers', [])

    def send(self, message: str) -> bool:
        if not self.enabled:
            return False

        # SMS는 160자 제한이 있으므로 핵심 정보만
        truncated = message[:155] + '...' if len(message) > 158 else message
        success = True

        for to_number in self.to_numbers:
            try:
                url = (
                    f"https://api.twilio.com/2010-04-01/Accounts/"
                    f"{self.account_sid}/Messages.json"
                )
                data = urllib.parse.urlencode({
                    'To': to_number,
                    'From': self.from_number,
                    'Body': truncated,
                }).encode('utf-8')

                import base64
                credentials = base64.b64encode(
                    f"{self.account_sid}:{self.auth_token}".encode()
                ).decode()

                req = urllib.request.Request(url, data=data)
                req.add_header('Authorization', f'Basic {credentials}')

                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 201:
                        logger.info(f"SMS 발송 완료: {to_number}")
                    else:
                        logger.warning(f"SMS 응답 코드: {resp.status}")
                        success = False
            except Exception as e:
                logger.error(f"SMS 발송 실패 ({to_number}): {e}")
                success = False

        return success


class WebhookNotifier:
    """Webhook 알림 (Slack, Teams 등)"""

    def __init__(self, config: dict):
        self.enabled = config.get('enabled', False)
        if not self.enabled:
            return
        self.urls = config.get('urls', [])
        self.timeout = config.get('timeout_seconds', 10)

    def send(self, title: str, body: str, severity: str) -> bool:
        if not self.enabled:
            return False

        color_map = {
            'CRITICAL': '#d32f2f',
            'HIGH': '#f57c00',
            'MEDIUM': '#fbc02d',
            'LOW': '#388e3c',
            'NORMAL': '#757575',
        }
        color = color_map.get(severity, '#757575')

        # Slack 형식 payload
        payload = json.dumps({
            'attachments': [{
                'color': color,
                'title': title,
                'text': body,
                'footer': 'Java Log Monitor',
                'ts': int(datetime.now().timestamp()),
            }]
        }).encode('utf-8')

        success = True
        for url in self.urls:
            try:
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={'Content-Type': 'application/json'},
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status in (200, 201, 204):
                        logger.info(f"Webhook 발송 완료: {url[:50]}...")
                    else:
                        success = False
            except Exception as e:
                logger.error(f"Webhook 발송 실패: {e}")
                success = False

        return success


class AlertNotifier:
    """알림 통합 관리자"""

    def __init__(self, config: dict):
        alert_cfg = config.get('alerting', {})
        scoring_cfg = config.get('scoring', {})

        self.email = EmailNotifier(alert_cfg.get('email', {}))
        self.sms = SMSNotifier(alert_cfg.get('sms', {}))
        self.webhook = WebhookNotifier(alert_cfg.get('webhook', {}))
        self.throttler = AlertThrottler(
            cooldown_seconds=scoring_cfg.get('alert_cooldown_seconds', 300)
        )

    def send_alert(self, result: ScoringResult, algo_details: str) -> bool:
        """스코어링 결과 기반 알림 발송"""
        alert_key = f"{result.exception_type}:{result.severity_level}"

        if not self.throttler.can_send(alert_key):
            logger.debug(f"알림 쿨다운 중: {alert_key}")
            return False

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        subject = (
            f"[{result.severity_level}] Java Log 이상 탐지 - "
            f"{result.exception_type}"
        )

        body = (
            f"=== Java Log Anomaly Alert ===\n"
            f"시각: {now}\n"
            f"심각도: {result.severity_level}\n"
            f"Exception: {result.exception_type}\n"
            f"발생 건수: {result.count_in_window}\n"
            f"최종 스코어: {result.final_score:.3f}\n"
            f"\n--- 스코어 상세 ---\n"
            f"{result.detail}\n"
            f"\n--- 알고리즘 분석 ---\n"
            f"{algo_details}\n"
        )

        sms_message = (
            f"[{result.severity_level}] {result.exception_type} "
            f"이상탐지 (스코어:{result.final_score:.2f}, "
            f"건수:{result.count_in_window})"
        )

        sent = False
        if self.email.enabled:
            sent = self.email.send(subject, body) or sent
        if self.sms.enabled:
            sent = self.sms.send(sms_message) or sent
        if self.webhook.enabled:
            sent = self.webhook.send(subject, body, result.severity_level) or sent

        if sent:
            self.throttler.record_sent(alert_key)

        return sent
