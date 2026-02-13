
from PIL import Image
from io import BytesIO
from django.core.files import File
from io import BytesIO
import os

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
