import smtplib
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Notification, NotificationChannel


def send_email(db: Session, user_id, to_email: str, subject: str, body: str, application_id=None) -> bool:
    """Send via Gmail SMTP with an App Password. Logs the attempt as a Notification row either way."""
    notification = Notification(
        user_id=user_id,
        application_id=application_id,
        channel=NotificationChannel.EMAIL,
        message=f"{subject}\n\n{body}",
        status="pending",
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)

    if not settings.smtp_username or not settings.smtp_app_password:
        notification.status = "failed"
        notification.sent_at = None
        db.commit()
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.notification_from_email or settings.smtp_username
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_username, settings.smtp_app_password)
            server.send_message(msg)
        notification.status = "sent"
        from datetime import datetime

        notification.sent_at = datetime.utcnow()
        db.commit()
        return True
    except Exception as exc:
        notification.status = "failed"
        notification.message += f"\n\nError: {exc}"
        db.commit()
        return False
