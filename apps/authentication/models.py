import datetime
from django.db import models
from django.contrib.auth.models import AbstractUser, PermissionsMixin,BaseUserManager
# Create your models here.
from django.utils.translation import gettext_lazy as _
from pytz import utc
from apps.utils.uuid_generator import generate_custom_id


GENDER_CHOICES = (
    ("male", "male"),
    ("female", "female"),

)


class MyUserManager(BaseUserManager):
    def create_user(self, email, username='',first_name='ME',last_name='You', password=None):
        """
        Creates and saves a User with the given email, date of
        birth and password.
        """
        if not email:
            raise ValueError("Users must have an email address")

        user = self.model(
            email=self.normalize_email(email),username=username,first_name=first_name,last_name=last_name,
            # date_of_birth=date_of_birth,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self,email,password):
        user = self.create_user(email=email,password=password)
        user.is_admin = True
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.save(using=self._db)
        return user



class User(AbstractUser, PermissionsMixin):
    id = models.CharField(
        primary_key=True,
        max_length=50,
        default=generate_custom_id,
        editable=False,
    )
    first_name = models.CharField(max_length=200,default='',blank=False,)    
    last_name = models.CharField(max_length=200,default='',blank=False)
    phone_number = models.CharField(max_length=200,default='',blank=True)
    email = models.EmailField(default='',unique=True)
    password = models.CharField(_("password"), max_length=128,blank=True)
    username = models.CharField(max_length=200,default='',blank=True)
    gender = models.CharField(max_length=200,default='',blank=True)
    date_of_birth = models.DateField(default=None,null=True)
    profile = models.JSONField(default=dict)
    date_time_added = models.DateTimeField(auto_now=True,null=True,)
    is_active = models.BooleanField(_("active"),
        default=False,
        help_text=_(
            "Designates whether this user should be treated as active. "
            "Unselect this instead of deleting accounts."
        ),)
    is_deleted = models.BooleanField(default=False)
    objects = MyUserManager()
    USER_TYPE_CHOICES = (
        ('customer', _('Customer')),
        ('designer', _('Designer')),
        ('admin', _('Admin')),
    )

    user_type = models.CharField(
        max_length=20,
        choices=USER_TYPE_CHOICES,
        default='customer',
        help_text=_("Defines the role of the user on Urbana platform.")
    )

    phone_number = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        unique=True,
        help_text=_("Contact phone number with country code (e.g., +234... for Nigeria).")
    )

    bio = models.TextField(
        blank=True,
        null=True,
        help_text=_("Short bio to showcase designer's story or customer's fashion interests.")
    )

    profile_picture = models.ImageField(
        upload_to='profile_pics/',
        blank=True,
        null=True,
        help_text=_("Profile image to personalize the user's presence on Urbana.")
    )

    # Updated: Now uses ForeignKey to Country model for rich data & flag display
    made_in_country_code =models.CharField(max_length=250)
    made_in_country_name =models.CharField(max_length=250)

    is_verified = models.BooleanField(
        default=False,
        help_text=_("Indicates if the user's email/phone is verified for secure access.")
    )

    date_joined = models.DateTimeField(auto_now_add=True)



    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")
        ordering = ['-date_joined']



    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    
    def __str__(self):
        return self.email
    
class DeletedUser(models.Model):
    id = models.CharField(
        primary_key=True,
        max_length=50,
        default=generate_custom_id,
        editable=False,
    )
    
    first_name = models.CharField(max_length=200,default='',blank=False,)    
    last_name = models.CharField(max_length=200,default='',blank=False)
    phone_number = models.CharField(max_length=200,default='',blank=True)
    email = models.EmailField(default='',blank=True)
    gender = models.CharField(max_length=200,default='',blank=True)
    date_of_birth = models.DateField(default=None,null=True)
    profile_type = models.CharField(default='',max_length=200,blank=True)
    date_time_added = models.DateTimeField(auto_now=True,null= True, )
    def __str__(self):
        return self.email
    

class VerificationCode(models.Model):
    id = models.CharField(
        primary_key=True,
        max_length=50,
        default=generate_custom_id,
        editable=False,
    )
    
    user=models.ForeignKey(User,on_delete=models.CASCADE,default='')
    code = models.TextField(default='',max_length=5000)


class PasswordResetCode(models.Model):
    id = models.CharField(
        primary_key=True,
        max_length=50,
        default=generate_custom_id,
        editable=False,
    )
    
    user=models.ForeignKey(User,on_delete=models.CASCADE,default='')
    code = models.TextField(default='',max_length=5000)



class Security(models.Model):
    id = models.CharField(
        primary_key=True,
        max_length=50,
        default=generate_custom_id,
        editable=False,
    )
    
    user = models.ForeignKey(User, on_delete=models.CASCADE,blank=True,null= True,)
    secret_question = models.CharField(max_length=225,blank=True,default='')
    secret_answer = models.CharField(max_length=225,blank=True,default='')
    previous_email = models.CharField(max_length=225,blank=True,default='')
    last_token = models.CharField(max_length=225,blank=True,default='')
    profile_updated = models.BooleanField(default=False,blank=True)
    suspension_count = models.IntegerField(default=0,blank=True)
    briefly_suspended = models.BooleanField(default=False,blank=True)
    time_suspended = models.DateTimeField(null=True, blank=True)
    time_suspended_timestamp = models.IntegerField(default=0,blank=True)
    locked = models.BooleanField(default=False)
    phone_number = models.CharField(max_length=45, blank=True, default ='')
    email_confirmed = models.BooleanField(default=False)
    two_factor_auth_enabled = models.BooleanField(default=False)
    email_change_request = models.BooleanField(default=False)
    pending_email = models.EmailField(default='',blank=True)
    login_attempt_count = models.IntegerField(default=0)
    class Meta:
        db_table = 'security'


    def save(self,*args, **kwargs):

        if self.suspension_count>2:
            self.briefly_suspended = True
            self.time_suspended =  datetime.datetime.now(utc)
            self.time_suspended_timestamp = datetime.datetime.now(utc).timestamp()
        secret_question=''
        for char in self.secret_question:
            if char ==  '?':
                continue
            else:
                secret_question = secret_question +char
        self.secret_question = secret_question+'?'
        super(Security,self).save()

class Picture(models.Model):
    id = models.CharField(
        primary_key=True,
        max_length=50,
        default=generate_custom_id,
        editable=False,
    )
    
    user=models.OneToOneField(User,on_delete=models.CASCADE)
    image=models.ImageField(null=True,blank=True)

    def __str__(self):
        return f"{self.user.first_name}: PROFILE"

    class Meta:
        db_table='pictures'



    # def delete(self):
    #     self.is_deleted = True
    #     super(Profile,self).save()