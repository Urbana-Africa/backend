import logging
from apps.utils.email_sender import resend_sendmail, wrap_email_html
from .models import EmailLog

logger = logging.getLogger(__name__)

def get_social_links_html(social_media_links):
    if not social_media_links:
        return ""
    links_html = []
    # If instagram is present, we highlight it
    if 'instagram' in social_media_links:
        links_html.append(f'<a href="{social_media_links["instagram"]}" style="margin: 0 10px; color: #ec6d13; text-decoration: none; font-weight: bold;">Instagram</a>')
    for platform, url in social_media_links.items():
        if platform.lower() == 'instagram':
            continue
        links_html.append(f'<a href="{url}" style="margin: 0 10px; color: #8c7561; text-decoration: none;">{platform.title()}</a>')
    
    if links_html:
        return f'<div style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #e3dbd3; font-size: 14px;"><p style="margin: 0 0 10px 0; color: #1b1c1a; font-weight: bold;">Designer Socials:</p>{" | ".join(links_html)}</div>'
    return ""

def render_lead_email(lead, subject, html_body):
    """Render a marketing email body for a lead — placeholder substitution,
    social links and the unsubscribe footer. ``lead=None`` renders sample
    placeholders for test sends."""
    from .eligibility import unsubscribe_url_for

    brand = lead.brand_name if lead else "Sample Brand"
    designer = (lead.designer_name or lead.brand_name) if lead else "Sample Designer"
    socials = lead.social_media_links if lead else {}

    if '{{ designer_name }}' in html_body:
        html_body = html_body.replace('{{ designer_name }}', designer)
    if '{{ brand_name }}' in html_body:
        html_body = html_body.replace('{{ brand_name }}', brand)

    unsubscribe_url = unsubscribe_url_for(lead) if lead else "#"
    return f"""
    <div style="font-family: 'Plus Jakarta Sans', Arial, sans-serif; color: #1b1c1a; line-height: 1.6;">
        {html_body}
        {get_social_links_html(socials)}
        <p style="margin-top: 24px; font-size: 11px; color: #8c7561;">
            You are receiving this because your brand was identified as an
            independent fashion designer. <a href="{unsubscribe_url}" style="color: #8c7561;">Unsubscribe</a>
            from future outreach.
        </p>
    </div>
    """


def compile_and_send_lead_email(lead, template, custom_html_body=None,
                                custom_subject=None, campaign=None):
    """Compiles and sends a marketing email to a lead.

    Enforces contact eligibility (MKT-04): suppressed, unreviewed, unqualified
    or frequency-capped leads are never sent — a ``Suppressed``/``Skipped``
    EmailLog row is written instead so the block is auditable.
    """
    from .eligibility import lead_contactable

    subject = custom_subject or (template.subject if template else '')
    html_body = custom_html_body or (template.html_body if template else '')

    ok, reasons = lead_contactable(lead)
    if not ok:
        logger.info("Lead email blocked for %s: %s", lead.brand_name, reasons)
        EmailLog.objects.create(
            campaign=campaign,
            lead=lead,
            subject=subject or '(no subject)',
            status='Suppressed' if any('suppress' in r for r in reasons) else 'Skipped',
            reason='; '.join(reasons)[:255],
        )
        return False

    if not (subject and html_body):
        logger.error("Cannot send email to %s: missing subject or body", lead.brand_name)
        return False

    final_html = render_lead_email(lead, subject, html_body)

    try:
        final_html = wrap_email_html(final_html, subject)
        if not resend_sendmail(
            subject=subject,
            recipient_list=[lead.email],
            message=final_html,
            from_name="Urbana Africa Marketing"
        ):
            raise RuntimeError("resend_sendmail returned False")
        EmailLog.objects.create(
            campaign=campaign,
            lead=lead,
            subject=subject,
            status='Sent'
        )
        return True
    except Exception as e:
        logger.error(f"Error sending email to {lead.email}: {e}")
        EmailLog.objects.create(
            campaign=campaign,
            lead=lead,
            subject=subject,
            status='Failed',
            reason=str(e)[:255],
        )
        return False
