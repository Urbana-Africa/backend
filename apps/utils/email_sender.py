"""Email sending infrastructure for Urbana.

All transactional / marketing email goes through :func:`resend_sendmail`, which
uses the Resend SMTP relay configured in ``settings.py``.  The legacy raw-SMTP
``sendmail`` helper is retained for backwards compatibility with the two
packaging-media call sites, but now routes through the same Resend connection
instead of separate SMTP credentials.

Design notes
------------
* Failures are logged via the ``apps.utils.email_sender`` logger and surfaced as
  a ``False`` return value — they are **never** silently swallowed.
* The social/footer block is **not** appended here.  Every email template
  extends ``emails/base.html`` which already renders a branded footer, so
  appending a second footer produced duplicated footers.  Raw-HTML emails that
  do not use the base template are responsible for their own footer.
* No credentials are hardcoded — everything comes from settings / environment.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage, get_connection

logger = logging.getLogger(__name__)


def resend_sendmail(subject, recipient_list, message, from_email=None, from_name=None):
    """Send an HTML email via the Resend SMTP relay.

    Returns ``True`` on success, ``False`` on failure (failure is also logged).
    """
    sender = from_email or settings.DEFAULT_FROM_EMAIL
    if from_name:
        sender = f"{from_name} <{sender}>"

    recipients = [r for r in recipient_list if r]
    if not recipients:
        logger.warning("resend_sendmail called with no valid recipients (subject=%r)", subject)
        return False

    try:
        with get_connection(
            host=settings.RESEND_SMTP_HOST,
            port=settings.RESEND_SMTP_PORT,
            username=settings.RESEND_SMTP_USERNAME,
            password=settings.RESEND_API_KEY,
            use_tls=True,
        ) as connection:
            email = EmailMessage(
                subject=subject,
                body=message,
                to=recipients,
                from_email=sender,
                connection=connection,
            )
            email.content_subtype = "html"
            email.send()
        logger.info("Email sent: subject=%r recipients=%s", subject, recipients)
        return True
    except Exception as exc:  # network / provider / auth errors
        logger.error("Email send failed: subject=%r recipients=%s error=%s", subject, recipients, exc)
        return False


def sendmail(subject, recipient_list, message, customize=None, **kwargs):
    """Backwards-compatible wrapper kept for the packaging-media call sites.

    Routes through :func:`resend_sendmail` so a single provider/credential set
    is used.  The ``customize``/``emailid`` kwargs are accepted but ignored —
    they were only used by the removed foreign-project attachment helpers.
    """
    return resend_sendmail(subject, recipient_list, message)


# ============================================================
# Branded HTML wrapper for inline (non-template) emails
# ============================================================

_BRANDED_WRAPPER = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{subject}</title>
</head>
<body style="margin:0;padding:0;background-color:#fbf9f5;font-family:'Plus Jakarta Sans',Arial,sans-serif;color:#1b1c1a;line-height:1.6;">
  <center style="width:100%;background-color:#fbf9f5;">
    <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color:#fbf9f5;max-width:600px;margin:0 auto;">
      <!-- Header with logo -->
      <tr>
        <td style="background-color:#fbf9f5;padding:24px 32px;text-align:center;border-bottom:1px solid #eae8e4;">
          <a href="{site_url}" style="display:inline-block;text-decoration:none;">
            <img src="{logo_url}" alt="Urbana Africa" width="40" height="40" style="display:inline-block;vertical-align:middle;width:40px;height:40px;max-width:40px;border:0;outline:none;text-decoration:none;">
            <span style="display:inline-block;vertical-align:middle;margin-left:10px;font-family:'Manrope',Arial,sans-serif;font-size:24px;font-weight:800;text-transform:uppercase;letter-spacing:-0.05em;color:#1b1c1a;text-decoration:none;">Urbana Africa</span>
          </a>
        </td>
      </tr>
      <!-- Content -->
      <tr>
        <td style="padding:32px;">
          {body}
        </td>
      </tr>
      <!-- Footer -->
      <tr>
        <td style="background-color:#e4e2de;padding:40px 32px;text-align:center;">
          <span style="font-family:'Manrope',Arial,sans-serif;font-weight:700;font-size:18px;color:#1b1c1a;margin-bottom:16px;display:block;">Urbana Africa</span>
          <p style="font-size:12px;color:#41606e;margin-bottom:8px;">Connecting global homes with authentic African craftsmanship.</p>
          <p style="margin-top:24px;">
            <a href="{privacy_url}" style="color:#8d4b00;text-decoration:none;">Privacy Policy</a> &nbsp;|&nbsp;
            <a href="{terms_url}" style="color:#8d4b00;text-decoration:none;">Terms of Service</a> &nbsp;|&nbsp;
            <a href="{contact_url}" style="color:#8d4b00;text-decoration:none;">Contact Us</a>
          </p>
          <p style="margin-top:24px;font-size:11px;color:#41606e;">&copy; {year} Urbana Africa. Celebrating African Excellence.</p>
        </td>
      </tr>
    </table>
  </center>
</body>
</html>"""


def wrap_email_html(body_html, subject=""):
    """Wrap inline HTML email content in the branded Urbana layout.

    Use this for emails that are built as f-strings rather than Django
    templates (contact messages, support tickets, admin notifications,
    packaging-media emails) so they get the same logo header and footer
    as template-based emails.
    """
    from datetime import datetime
    return _BRANDED_WRAPPER.format(
        subject=subject,
        site_url=getattr(settings, "STORE_URL", "https://www.urbanaafrica.com"),
        logo_url=getattr(settings, "LOGO_URL", "https://www.urbanaafrica.com/urbana-icon.png"),
        privacy_url=getattr(settings, "PRIVACY_URL", "https://www.urbanaafrica.com/privacy"),
        terms_url=getattr(settings, "TERMS_URL", "https://www.urbanaafrica.com/terms"),
        contact_url=getattr(settings, "CONTACT_URL", "https://www.urbanaafrica.com/contact"),
        year=datetime.now().year,
        body=body_html,
    )
