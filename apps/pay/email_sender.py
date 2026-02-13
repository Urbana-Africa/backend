from datetime import datetime
from email.mime.image import MIMEImage
import smtplib  
import email.utils
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from general import models as gmodels
from padmin import models as amodels


def birthdayAttachment(msg,**kwargs):
    birthdayimages = gmodels.BirthdayImages.objects.all()
    for image in birthdayimages:
        fp = open(image.image.path, 'rb')
        msgImage = MIMEImage(fp.read())
        fp.close()
        msgImage.add_header('Content-ID', '<'+image.name+'>')
        msg.attach(msgImage)


def customSheduledEmailAttachments(msg,emailid=None):
    email = amodels.SheduledEmail.objects.get(id=emailid)
    atttachments = email.attachments.all()
    if email.body_image:
        body_image = email.body_image
        fp = open(body_image.path, 'rb')
        msgImage = MIMEImage(fp.read())
        fp.close()
        msgImage.add_header('Content-ID', '<body_image>')
        msg.attach(msgImage)
    for image in atttachments:
        fp = open(image.file.path, 'rb')
        msgImage = MIMEImage(fp.read())
        fp.close()
        msgImage.add_header('Content-ID', '<'+image.name+'>')
        msg.attach(msgImage)



def sendmail(RECIPIENT,BODY_TEXT,BODY_HTML,SUBJECT,customize = None,**kwargs):
    
    SENDER = 'info@dominionpartnersglobal.com'  
    SENDERNAME = 'DPG'
    RECIPIENT  = ', '.join(RECIPIENT)
    USERNAME_SMTP = "AKIAT72ANI6GD7RJWR5W"
    PASSWORD_SMTP = "BJ33OWZ1Z0gomEo4iICtF53j07s/rAUHAk3IEs7ZnRE0"
    HOST = "email-smtp.us-east-1.amazonaws.com"
    PORT = 587
    msg = MIMEMultipart('alternative')
    msg['Subject'] = SUBJECT
    msg['From'] = email.utils.formataddr((SENDERNAME, SENDER))
    msg['To'] = RECIPIENT
    part1 = MIMEText(BODY_TEXT, 'plain')
    part2 = MIMEText(BODY_HTML, 'html')
    msg.attach(part1)
    msg.attach(part2)
    try:
        settings = list(gmodels.General.objects.all())[0]
    except IndexError:
        settings =[]
    try:
        fp = open(settings.logo.path, 'rb')
        msgImage = MIMEImage(fp.read())
        fp.close()

        # Define the image's ID as referenced above
        msgImage.add_header('Content-ID', '<logo>')
        msg.attach(msgImage)
    except Exception:
        pass

    socials = gmodels.SocialLink.objects.all()
    for social in socials:
        try:
            fp = open(social.image.path, 'rb')
            msgImage = MIMEImage(fp.read())
            fp.close()
            msgImage.add_header('Content-ID', '<'+social.name+'>')
            msg.attach(msgImage)
        except Exception:
            pass
    
    if customize:
        customize(msg,emailid=kwargs['emailid'])
    # Try to send the message.
    try:  
        server = smtplib.SMTP(HOST, PORT)
        server.ehlo()
        server.starttls()
        #stmplib docs recommend calling ehlo() before & after starttls()
        server.ehlo()
        server.login(USERNAME_SMTP, PASSWORD_SMTP)
        server.sendmail(SENDER, RECIPIENT.split(','), msg.as_string())
        server.close()
    # Display an error message if something goes wrong.
    except Exception as e:
        print ("Error: ", e)
    else:
        
        print ("Email sent!")
        return True


def sendBirthday(RECIPIENT,BODY_TEXT,BODY_HTML,SUBJECT):
    sendmail(RECIPIENT,BODY_TEXT,BODY_HTML,SUBJECT,birthdayAttachment)

def send(RECIPIENT,BODY_TEXT,BODY_HTML,SUBJECT):
    sendmail(RECIPIENT,BODY_TEXT,BODY_HTML,SUBJECT)



def sendSheduled(RECIPIENT,BODY_TEXT,BODY_HTML,SUBJECT,emailid):
    if sendmail(RECIPIENT,BODY_TEXT,BODY_HTML,SUBJECT,customSheduledEmailAttachments,emailid=emailid):
        email = amodels.SheduledEmail.objects.get(id=emailid)
        email.last_sent = datetime.now()
