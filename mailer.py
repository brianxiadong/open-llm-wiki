import logging
import smtplib
from email.message import EmailMessage


logger = logging.getLogger(__name__)


class Mailer:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        use_ssl: bool = True,
        default_from: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_ssl = use_ssl
        self._default_from = default_from or username

    @property
    def enabled(self) -> bool:
        return bool(self._host and self._port and self._username and self._password and self._default_from)

    def send_password_reset(self, to_email: str, username: str, reset_url: str, site_name: str) -> None:
        subject = f"[{site_name}] 密码重置"
        text = (
            f"{username}，你好：\n\n"
            f"你收到这封邮件，是因为有人请求重置 {site_name} 账号密码。\n"
            f"请在 1 小时内访问下面的链接完成重置：\n\n"
            f"{reset_url}\n\n"
            "如果这不是你的操作，可以忽略此邮件。"
        )
        html = (
            f"<p>{username}，你好：</p>"
            f"<p>你收到这封邮件，是因为有人请求重置 <strong>{site_name}</strong> 账号密码。</p>"
            f"<p>请在 1 小时内访问下面的链接完成重置：</p>"
            f'<p><a href="{reset_url}">{reset_url}</a></p>'
            "<p>如果这不是你的操作，可以忽略此邮件。</p>"
        )
        self.send_message(to_email, subject, text, html)

    def send_email_verification(self, to_email: str, username: str, verify_url: str, site_name: str) -> None:
        subject = f"[{site_name}] 邮箱验证"
        text = (
            f"{username}，你好：\n\n"
            f"欢迎注册 {site_name}。\n"
            "请先完成邮箱验证后再登录。\n"
            "请在 1 小时内访问下面的链接完成验证：\n\n"
            f"{verify_url}\n\n"
            "如果这不是你的操作，可以忽略此邮件。"
        )
        html = (
            f"<p>{username}，你好：</p>"
            f"<p>欢迎注册 <strong>{site_name}</strong>。</p>"
            "<p>请先完成邮箱验证后再登录。</p>"
            "<p>请在 1 小时内访问下面的链接完成验证：</p>"
            f'<p><a href="{verify_url}">{verify_url}</a></p>'
            "<p>如果这不是你的操作，可以忽略此邮件。</p>"
        )
        self.send_message(to_email, subject, text, html)

    def send_message(self, to_email: str, subject: str, text: str, html: str | None = None) -> None:
        if not self.enabled:
            raise RuntimeError("mail is not configured")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._default_from
        msg["To"] = to_email
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")

        smtp_cls = smtplib.SMTP_SSL if self._use_ssl else smtplib.SMTP
        with smtp_cls(self._host, self._port, timeout=20) as smtp:
            if not self._use_ssl:
                smtp.starttls()
            smtp.login(self._username, self._password)
            smtp.send_message(msg)
        logger.info("mail sent to %s", to_email)
