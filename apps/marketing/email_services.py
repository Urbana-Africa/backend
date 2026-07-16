import logging
from django.template.loader import render_to_string
from apps.utils.email_sender import resend_sendmail
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

def compile_and_send_lead_email(lead, template, custom_html_body=None, custom_subject=None):
    """
    Compiles the email body using the template (or custom body) and sends it.
    """
    subject = custom_subject or template.subject
    html_body = custom_html_body or template.html_body

    # Replace placeholders
    if '{{ designer_name }}' in html_body:
        name = lead.designer_name if lead.designer_name else lead.brand_name
        html_body = html_body.replace('{{ designer_name }}', name)
    if '{{ brand_name }}' in html_body:
        html_body = html_body.replace('{{ brand_name }}', lead.brand_name)

    # Compile with base.html
    context = {
        'subject': subject,
        'content': html_body,
        'lead_socials_html': get_social_links_html(lead.social_media_links)
    }

    # Add the social links HTML explicitly if it's not handled in base.html
    # We will inject it into the compiled message.
    compiled_message = render_to_string('emails/base.html', context)
    
    # Alternatively, if base.html doesn't render `content` directly but requires us to pass it:
    # Actually, we can just pass html_body + social_links.
    # The base.html from earlier seems to not have a block for `content` in our snippet, but usually it does.
    # Let's just wrap it in a div if we aren't sure how base.html is structured, but `resend_sendmail` 
    # might be able to just take our html. Let's just pass `html_body` + socials.

    final_html = f"""
    <div style="font-family: 'Plus Jakarta Sans', Arial, sans-serif; color: #1b1c1a; line-height: 1.6;">
        {html_body}
        {get_social_links_html(lead.social_media_links)}
    </div>
    """

    if not lead.email:
        logger.error(f"Cannot send email to {lead.brand_name}: No email address.")
        return False

    try:
        resend_sendmail(
            subject=subject,
            recipient_list=[lead.email],
            message=final_html,
            from_name="Urbana Africa Marketing"
        )
        # Log success
        EmailLog.objects.create(
            lead=lead,
            subject=subject,
            status='Sent'
        )
        return True
    except Exception as e:
        logger.error(f"Error sending email to {lead.email}: {e}")
        EmailLog.objects.create(
            lead=lead,
            subject=subject,
            status='Failed'
        )
        return False
