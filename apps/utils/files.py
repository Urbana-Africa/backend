
from PIL import Image
from io import BytesIO
from django.core.files import File
from io import BytesIO
import os
import hashlib
import base64

def reduce_image_size(image):
    if image.size/1000 > 100:
        img = Image.open(image)
        thumb_io = BytesIO()
        img_extention = str(image.name.split('.')[-1]).lower()
        if img_extention == 'jpg':
            img_extention = 'jpeg'
        img.save(thumb_io,img_extention, quality=50)
        new_image = File(thumb_io, name=image.name)
    else:
        new_image = image
    return new_image


def upload_student_files(instance, filename):
    """ this function has to return the location to upload the file """
 
    return os.path.join('students\\documents\\'+(instance.user.first_name +instance.user.last_name).lower(), filename)


def upload_resources(instance, filename):
    """ this function has to return the location to upload the file """

    filename = f'{instance.name}.{filename.split(".")[-1]}'
    if instance.file.name != f"{instance.name}.{instance.file.name.split('.')[-1]}":
        filename= f"{instance.name}.{instance.file.name.split('.')[-1]}"
        
    return os.path.join('resources\\', filename)



def upload_user_avatar(instance, filename):
    """ this function has to return the location to upload the file """
    return os.path.join('user\\files\\'+ instance.user.first_name + ' ' +instance.user.last_name+'\\avatar', filename)


def upload_school_file(instance, filename):
    """ this function has to return the location to upload the file """
    folder = instance.name.replace(' ','')
    return os.path.join('schools\\files\\'+ folder +'\\file', filename)

def generate_email_avatar(email, size=80):
    b64 = "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"
    return f"data:image/png;base64,{b64}"
