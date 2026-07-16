import os
import django
import sys

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'urbana.settings')
django.setup()

from apps.marketing.models import EmailTemplate

def seed_templates():
    templates = [
        {
            "name": "Initial Outreach (Discovered)",
            "subject": "Invitation to Join Urbana Africa Marketplace",
            "html_body": """
<p>Hi {{ designer_name }},</p>
<p>We recently discovered <strong>{{ brand_name }}</strong> and we are absolutely in love with your unique designs and commitment to authentic African craftsmanship.</p>
<p>At Urbana Africa, we are building a premium global marketplace dedicated to connecting talented African creators like you with international buyers.</p>
<p>We would love to invite you to join our platform. By becoming a seller, you gain access to our extensive network of shoppers, integrated global shipping logistics, and powerful marketing tools.</p>
<p>Are you available for a quick chat this week to discuss how we can help scale your brand globally?</p>
<p>Looking forward to connecting.</p>
<br>
<p>Best regards,</p>
<p>The Urbana Team</p>
            """
        },
        {
            "name": "Follow-up (In Discussion)",
            "subject": "Following up on your Urbana Africa Invitation",
            "html_body": """
<p>Hi {{ designer_name }},</p>
<p>I hope this email finds you well.</p>
<p>I am following up on our previous conversation regarding bringing <strong>{{ brand_name }}</strong> to the Urbana Africa marketplace.</p>
<p>As a reminder, our platform handles all international shipping complexities through our logistics partners, and we only charge a small commission on successful sales. There are zero upfront costs to join.</p>
<p>Let us know if you have any questions or if you're ready to take the next step. We're here to help!</p>
<br>
<p>Best regards,</p>
<p>The Urbana Team</p>
            """
        },
        {
            "name": "Onboarding Invite (Signed Up)",
            "subject": "Welcome to Urbana Africa! Next Steps to Onboard",
            "html_body": """
<p>Hi {{ designer_name }},</p>
<p>Welcome to the Urbana Africa family! We are thrilled to have <strong>{{ brand_name }}</strong> onboard.</p>
<p>To get your storefront live and start selling to a global audience, please complete your onboarding by following these next steps:</p>
<ol>
    <li>Log into your Designer Dashboard at designer.urbanaafrica.com</li>
    <li>Complete your brand profile and bio.</li>
    <li>Upload your initial catalog of products (we recommend starting with at least 5 key pieces).</li>
    <li>Submit your store for final approval.</li>
</ol>
<p>If you need any help with uploading your products, our support team is available 24/7.</p>
<br>
<p>Welcome aboard,</p>
<p>The Urbana Team</p>
            """
        }
    ]

    for t in templates:
        EmailTemplate.objects.update_or_create(
            name=t['name'],
            defaults={
                'subject': t['subject'],
                'html_body': t['html_body'].strip()
            }
        )
    print("Email templates seeded successfully!")

if __name__ == '__main__':
    seed_templates()
