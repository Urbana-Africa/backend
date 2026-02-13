import smtplib  
import email.utils
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import ssl
from django.contrib.auth.models import User
from django.core.mail import EmailMessage, get_connection
from django.conf import settings



def resend_sendmail(subject,recipient_list,message):

    subject = subject
    recipient_list = recipient_list
    from_email = "team@schoolmummy.com"
    message = message


    with get_connection(
        host=settings.RESEND_SMTP_HOST,
        port=settings.RESEND_SMTP_PORT,
        username=settings.RESEND_SMTP_USERNAME,
        password=settings.RESEND_API_KEY,
        use_tls=True,
        ) as connection:
            r = EmailMessage(
                  subject=subject,
                  body=message,
                  to=recipient_list,
                  from_email=from_email,
                  connection=connection)
            r.content_subtype = 'html'
            r.send()
    print('Sent')
    return True



def sendmail(subject,recipient_list,message,customize = None,**kwargs):
    SENDER ="urbana"
    SENDERNAME ="urbana"
    RECIPIENT  = ', '.join(recipient_list)
    SMTP_USER = settings.SMTP_USER
    SMTP_PASSWORD = settings.SMTP_PASSWORD
    HOST = settings.SMTP_HOST
    PORT = settings.SMTP_PORT
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = email.utils.formataddr((SENDERNAME, SENDER))
    msg['To'] = RECIPIENT
    part1 = MIMEText(message, 'plain')
    part2 = MIMEText(message, 'html')
    msg.attach(part1)
    msg.attach(part2)

    # try:
    #     fp = open(settings.logo.path, 'rb')
    #     msgImage = MIMEImage(fp.read())
    #     fp.close()
    #     msgImage.add_header('Content-ID', '<logo>')
    #     msg.attach(msgImage)
    # except Exception:
    #     pass

    if customize:
        customize(msg,emailid=kwargs['emailid'])
        
    # socials = gmodels.SocialLink.objects.all()
    # for social in socials:
    #     try:
    #         fp = open(social.image.path, 'rb')
    #         msgImage = MIMEImage(fp.read())
    #         fp.close()
    #         msgImage.add_header('Content-ID', '<'+social.name+'>')
    #         msg.attach(msgImage)
    #     except Exception:
    #         pass
    context = ssl.create_default_context()
   

    with smtplib.SMTP(HOST, PORT) as server:
        try:
            server.ehlo()  # Can be omitted
            server.starttls(context=context)
            server.ehlo()  # Can be omitted
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, RECIPIENT.split(','), msg.as_string())
            server.close()
        except Exception as e:
            raise ValueError(e)
        else:
            return True



def login_server(SMTP_USER,SMTP_PASSWORD,CREDENTIALS):
    HOST = CREDENTIALS['EMAIL_HOST']
    PORT = CREDENTIALS['EMAIL_PORT']
    context = ssl.create_default_context()
  
    with smtplib.SMTP(HOST, PORT) as server:
        server.ehlo()  # Can be omitted
        server.starttls(context=context)
        server.ehlo()  # Can be omitted
        server.login(SMTP_USER, SMTP_PASSWORD)
        return server

def prepare_message(RECIPIENT,BODY_TEXT,BODY_HTML,SUBJECT,CREDENTIALS, customize = None,**kwargs):
    SENDER = CREDENTIALS['EMAIL_HOST_USER']
    SENDERNAME = CREDENTIALS['SENDER']
    RECIPIENT  = ', '.join(RECIPIENT)
    USERNAME_SMTP = CREDENTIALS['EMAIL_HOST_USER']
    PASSWORD_SMTP = CREDENTIALS['EMAIL_HOST_PASSWORD']
    HOST = CREDENTIALS['EMAIL_HOST']
    PORT = CREDENTIALS['EMAIL_PORT']
    msg = MIMEMultipart('alternative')
    msg['Subject'] = SUBJECT
    msg['From'] = email.utils.formataddr((SENDERNAME, SENDER))
    msg['To'] = RECIPIENT
    part1 = MIMEText(BODY_TEXT, 'plain')
    part2 = MIMEText(BODY_HTML, 'html')
    msg.attach(part1)
    msg.attach(part2)

    # try:
    #     fp = open(settings.logo.path, 'rb')
    #     msgImage = MIMEImage(fp.read())
    #     fp.close()
    #     msgImage.add_header('Content-ID', '<logo>')
    #     msg.attach(msgImage)
    # except Exception:
    #     pass

    if customize:
        customize(msg,emailid=kwargs['emailid'])
    return msg


def send_personalized_broadcast_mail(server:'smtplib.SMTP',msg,scheduled_email, receiver:User,SMTP_USER):
    RECIPIENT  = ', '.join([receiver.email])

    try:
        server.sendmail(SMTP_USER, RECIPIENT.split(','),msg.as_string())
        print('Email sent to', RECIPIENT.split(','))
        scheduled_email.successful_array = scheduled_email.successful_array+ f'{receiver.pk},'
        scheduled_email.batch_sent +=1
        scheduled_email.save() 
        return True
    except Exception as e:
        # print("Email not sent")
        print(SMTP_USER)
        print(e)
        return False
        # print(f'error {SMTP_USERS[user]}')
        # raise ValueError(e)
    

