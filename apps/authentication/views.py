from random import random
import threading
from django.http import JsonResponse
from rest_framework.views import APIView,status
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from apps.customers.models import Customer
from apps.designers.models import Designer
from apps.utils.email_sender import resend_sendmail
from .serializers import *
from rest_framework import status
from django.contrib.auth import logout
from rest_framework.permissions import IsAuthenticated,AllowAny
from django.views.decorators.csrf import ensure_csrf_cookie
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.hashers import check_password,make_password
from apps.authentication.models import DeletedUser, PasswordResetCode, User, VerificationCode
import secrets
import string
from django.utils.decorators import method_decorator
from social_django.utils import psa
from django.conf import settings


def getUserData(request):
    user= request.user
    try:
        serialized_user = UserSerializer(user,many=False).data
    except Exception:
        serialized_user = False
    return serialized_user


class CustomTokenObtainPairView(TokenObtainPairView):
    """
    Custom login endpoint for Refresh Ghana:
    - Supports login with email OR username
    - Checks is_deleted and is_active
    - Sets HttpOnly cookies for access & refresh tokens (secure for SPA)
    - Returns minimal user data (no tokens in body to reduce exposure)
    """
    def post(self, request, *args, **kwargs):
        print(request.data)
        email_or_username = str(request.data.get("email", "")).lower().strip()
        password = str(request.data.get("password", "")).strip()
        print(email_or_username)

        if not email_or_username or not password:
            return Response(
                {"status": "error", "message": "Email/Username and password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Try email first, then username
            user = User.objects.get(email=email_or_username, is_deleted=False)
        except User.DoesNotExist:
            try:
                user = User.objects.get(username=email_or_username, is_deleted=False)
            except User.DoesNotExist:
                return Response(
                    {"status": "error", "message": "Invalid credentials"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

        if not check_password(password, user.password):
            return Response(
                {"status": "error", "message": "Invalid credentials"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {
                    "status": "success",
                    "inactive": True,
                    "user": UserSerializer(user).data,
                },
                status=status.HTTP_200_OK,
            )

        # Generate tokens using SimpleJWT
        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)
        refresh_token = str(refresh)

        # Prepare response (no tokens in body → more secure)
        response = Response(
            {
                "status": "success",
                "message": "Login successful",
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )

        # Set secure HttpOnly cookies (use your settings values!)
        from django.conf import settings

        response.set_cookie(
            key=settings.SIMPLE_JWT["AUTH_COOKIE"],  # "access_token"
            value=access_token,
            max_age=settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds(),
            httponly=True,
            secure=settings.SIMPLE_JWT["AUTH_COOKIE_SECURE"],  # False in dev, True in prod
            samesite=settings.SIMPLE_JWT["AUTH_COOKIE_SAMESITE"],  # "Lax" in dev, "None" in prod if needed
            path="/",
            domain=settings.SIMPLE_JWT["AUTH_COOKIE_DOMAIN"],  # ".urbana.local" or ".urbanaafrica.com"
        )

        response.set_cookie(
            key=settings.SIMPLE_JWT["AUTH_COOKIE_REFRESH"],  # "refresh_token"
            value=refresh_token,
            max_age=settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds(),
            httponly=True,
            secure=settings.SIMPLE_JWT["AUTH_COOKIE_SECURE"],
            samesite=settings.SIMPLE_JWT["AUTH_COOKIE_SAMESITE"],
            path="/",
            domain=settings.SIMPLE_JWT["AUTH_COOKIE_DOMAIN"],
        )

        return response




class CustomTokenRefreshView(TokenRefreshView):
    """
    Refresh access token using HttpOnly refresh cookie.
    Returns new access token via cookie (not response body).
    """

    def post(self, request, *args, **kwargs):
        refresh_token = request.COOKIES.get(
            settings.SIMPLE_JWT["AUTH_COOKIE_REFRESH"]
        )

        if not refresh_token:
            return Response(
                {"message": "No refresh token", "status": "error"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = self.get_serializer(data={"refresh": refresh_token})

        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            return Response(
                {"message": "Invalid or expired refresh token", "status": "error"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        access_token = serializer.validated_data.get("access")

        res = Response(
            {
                "status": "success",
                "refreshed": True,
            },
            status=status.HTTP_200_OK,
        )

        res.set_cookie(
            key=settings.SIMPLE_JWT["AUTH_COOKIE"],  # "access_token"
            value=access_token,
            max_age=settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds(),
            httponly=True,
            secure=settings.SIMPLE_JWT["AUTH_COOKIE_SECURE"],
            samesite=settings.SIMPLE_JWT["AUTH_COOKIE_SAMESITE"],
            path="/",
            domain=settings.SIMPLE_JWT["AUTH_COOKIE_DOMAIN"],
        )

        return res




@method_decorator(ensure_csrf_cookie,'get')
class SetCSRFCookie(APIView):
    permission_classes = ([IsAuthenticated])

    def get(self,request):
        return JsonResponse({
            "csrftoken": request.META.get("CSRF_COOKIE", "")
        })



class Logout(APIView):
    permission_classes = ([])
    authentication_classes=()

    def post(self, request):
        try:
            logout(request)
            response = Response({'success': True, 'status': 'success'})
            
            # 🍪 Clear HttpOnly JWT cookies
            response.delete_cookie(
                settings.SIMPLE_JWT["AUTH_COOKIE"],
                path="/",
                domain=settings.SIMPLE_JWT["AUTH_COOKIE_DOMAIN"],
                samesite=settings.SIMPLE_JWT["AUTH_COOKIE_SAMESITE"],
            )
            response.delete_cookie(
                settings.SIMPLE_JWT["AUTH_COOKIE_REFRESH"],
                path="/",
                domain=settings.SIMPLE_JWT["AUTH_COOKIE_DOMAIN"],
                samesite=settings.SIMPLE_JWT["AUTH_COOKIE_SAMESITE"],
            )
            
            return response

        except Exception as e:
            print(f"Logout error: {e}")
            return Response({'success': False, 'message': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        



BUSINESS_NAME = "urbana"


def generate_verification_code(user):
    code =''.join(secrets.choice(string.digits) for x in range(6))
    existing_codes = VerificationCode.objects.filter(user=user)
    existing_codes.delete()
    VerificationCode.objects.create(user = user,code=make_password(code))
    return code

def generate_password_reset_code(user):
    code = ''.join(secrets.choice(string.digits) for x in range(6))
    existing_codes = PasswordResetCode.objects.filter(user=user)
    existing_codes.delete()
    PasswordResetCode.objects.create(user = user,code=make_password(code))
    return code



def setup_security(user):
    security = Security.objects.create(user=user)
    return True






def getuser(request,):
    try:
        user = User.objects.get(pk=request.user.pk)
    except Exception:
        user = False
    return user


class RequestVericationCodeView(APIView):

    queryset = Security.objects.all()
    serializer_class = SecuritySerializer
    authentication_classes = ()
    permission_classes = ()

    def post(self,request):
        email =request.data['email']
        try:
            user= User.objects.get(email=email)
            send_verification_email(user)
            data = {'status':'success'}
            return Response(data,status=status.HTTP_202_ACCEPTED)
        except ObjectDoesNotExist:
            return Response({'status':'error','message':'User not found'},status=status.HTTP_404_NOT_FOUND)
        
class CheckUsername(APIView):

    queryset = User.objects.all()
    serializer_class = UserSerializer


    def post(self,request):
        username =str(request.data['username']).lower()
        try:
            user= User.objects.get(username=username)
            return Response({'status':'success','exist':True},status=status.HTTP_202_ACCEPTED)
        except ObjectDoesNotExist:
            return Response({'status':'success','exist':False},status=status.HTTP_202_ACCEPTED)

class ChangeUsername(APIView):

    queryset = User.objects.all()
    serializer_class = UserSerializer


    def post(self,request):
        username =str(request.data['username']).lower()
        try:
            user= User.objects.get(username=username.lower())
            return Response({'status':'error','message':'Username already taken'},status=status.HTTP_400_BAD_REQUEST)
        except ObjectDoesNotExist:
            request.user.username = username
            request.user.save()
            return Response({'status':'success','user':getUserData(request)},status=status.HTTP_202_ACCEPTED)


class SetPassword(APIView):

    queryset = Security.objects.all()
    serializer_class = SecuritySerializer
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        verification_code = request.data['code']
        email = request.data['email']
        # '154914'
        print(request.data)
        try:
            user= User.objects.get(email=email)
            try:
                verification = VerificationCode.objects.get(user=user)
                if check_password(verification_code,verification.code):
                    user.set_password(request.data['password'])
                    user.save()
                    verification.delete()
                    return Response({'status':'success'},status=status.HTTP_202_ACCEPTED)
                else:
                    data= {'message':'Invalid verification code','status':'error'}
                return Response(data,status=status.HTTP_202_ACCEPTED)

            except ObjectDoesNotExist:
                return Response({'status':'error','message':'Invalid code'},status=status.HTTP_404_NOT_FOUND)


        except ObjectDoesNotExist:
            return Response({'status':'error','message':'User not found'},status=status.HTTP_404_NOT_FOUND)



class SetEmail(APIView):

    queryset = Security.objects.all()
    serializer_class = SecuritySerializer
    permission_classes = ()


    def post(self, request):
        verification_code = request.data['code']
        email = request.data['email']
        # '154914'
        if request.user.is_authenticated:
                user= request.user
        else:
            try:
                user= User.objects.get(email=email)
            except ObjectDoesNotExist:
                return Response({'status':'error','message':'User not found'},status=status.HTTP_404_NOT_FOUND)

        try:
            verification = VerificationCode.objects.get(user=user)
            if check_password(verification_code,verification.code):
                user.email = email
                user.username = email
                user.save()
                verification.delete()
                return Response({'user':getUserData(request),'status':'success'},status=status.HTTP_202_ACCEPTED)
            else:
                data= {'message':'Invalid verification code','status':'error'}
            return Response(data,status=status.HTTP_202_ACCEPTED)

        except ObjectDoesNotExist:
            return Response({'status':'error','message':'Bad request'},status=status.HTTP_404_NOT_FOUND)



class ChangePassword(APIView):
    permission_classes = ([IsAuthenticated])
    queryset = Security.objects.all()
    serializer_class = SecuritySerializer


    def post(self, request):
        try:
            user = request.user
            user.set_password(request.data['password'].strip())
            user.save()
            response = {}       
            response['status'] = 'success'              
            return Response(response,status=status.HTTP_202_ACCEPTED)
        except Exception as e:
            return Response({'status':'error','message':str(e)},status=status.HTTP_400_BAD_REQUEST)



class VerifyCode(APIView):

    queryset = Security.objects.all()
    serializer_class = SecuritySerializer
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        verification_code = str(request.data['code']).strip()
        email = request.data['email']
        # '154914'
        try:
            user= User.objects.get(email=email)
            try:
                code = VerificationCode.objects.get(user=user)
                if check_password(verification_code,code.code):
                    return Response({'status':'success'},status=status.HTTP_202_ACCEPTED)

                else:
                    return Response({'message':'Invalid verification code','status':'error'},status=status.HTTP_200_OK)
            except ObjectDoesNotExist:
                return Response({'status':'error','message':'Invalid code'},status=status.HTTP_404_NOT_FOUND)
        except ObjectDoesNotExist:
            return Response({'status':'error','message':'User not found'},status=status.HTTP_404_NOT_FOUND)



class CheckEmail(APIView):
    permission_classes = ()
    serializer_class = UserSerializer
    def post(self,request,*args,**kwargs):
        alldata = dict(request.POST)
        datas = {}
        for data in alldata:
            data = str(data).lower()
            datas[data] = str(alldata[data][0]).lower()
        try:       
            try:
                user=User.objects.get(**datas)
                return Response({
                                    'user_exist':True,
                                    })
            except ObjectDoesNotExist:
                return Response({
                    'user_exist':False,
                    })  
   
        except Exception:
                return Response({
                    'status':'Failed',
                    'data':request.data
                    })                           
        
class LoginView(APIView):
    permission_classes = (AllowAny,)
    authentication_classes = ()
    serializer_class = UserSerializer


    def post(self, request,):
        email = str(request.data["email"]).lower().strip()
        password = str(request.data["password"]).strip()
        try:
            user = User.objects.get(email = email)
        except ObjectDoesNotExist:
            return Response({'status':'error','message':'Invalid credentials'},status=status.HTTP_404_NOT_FOUND)
        if check_password(password,user.password):
            response = {}
            serialized_user = UserSerializer(user)
            response['data'] = serialized_user.data
            response['status'] = 'success'
            return Response(response,status=status.HTTP_202_ACCEPTED)
        else:
            return Response({'status':'error','message':'Invalid credentials'},status=status.HTTP_404_NOT_FOUND)



def send_verification_email(user:User):
    try:
        VerificationCode.objects.get(user=user).delete()
        code = VerificationCode.objects.create(user=user)
    except ObjectDoesNotExist:
        code = VerificationCode.objects.create(user=user)
    code_digits = str(round(9999999 * random()))[0:6]
    code.code= make_password(code_digits)
    code.save()
    print(code_digits)
    message = """<p>Hi there!,<br> <br>
    <b>Use """ + code_digits + """ as your activation code</b></p>"""
    subject = 'urbana   - Account creation'
    threading.Thread(target=resend_sendmail,args=(subject, [user.email], message,)).start()
    return True



class ResendVerificationCode(APIView):
    authentication_classes = ()
    permission_classes = ()


    def post(self,request):
        email = str(request.data['email']).lower()
        try:
            user = User.objects.get(email=email)
            send_verification_email(user)
        except ObjectDoesNotExist:
            return Response({'message':'User not found','status':'error'},status=status.HTTP_404_NOT_FOUND)
        return Response({'status':'success'},status=status.HTTP_200_OK)




class VerifyEmail(APIView):

    queryset = VerificationCode.objects.all()
    serializer_class = VerificationSerializer
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        verification_code = str(request.data['code']).strip()
        email = request.data['email']

        try:
            user= User.objects.get(email=email)
            try:
                code = VerificationCode.objects.get(user=user)
                if check_password(verification_code,code.code):
                    data = {'status':'success'}
                    user.is_active = True
                    user.save()
                    code.delete()       
                    data['data'] = UserSerializer(user,many=False).data                    
                    data['status'] = 'success'          
                    
                    # Create appropriate profile
                    if user.user_type == 'designer':
                        Designer.objects.get_or_create(user=user)
                    else:
                        Customer.objects.get_or_create(user=user)
                else:
                    data= {'status':'error','message':'invalid code'}
                return Response({**data},status=status.HTTP_200_OK)

            except ObjectDoesNotExist:
                return Response({'status':'error','message':'Invalid code'},status=status.HTTP_404_NOT_FOUND)


        except ObjectDoesNotExist:
            return Response({'status':'error','message':'user does not exist',},status=status.HTTP_404_NOT_FOUND)


class AllUserView(APIView):

    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = ()
    # permission_classes = ([IsAuthenticated])

    def get(self,request):

        user = User.objects.all()
        user = UserSerializer(user,many = True )
        if len(user.data) > 0:
            return Response(user.data,status=status.HTTP_200_OK)

        return Response({'no_user':True},status=status.HTTP_400_BAD_REQUEST)


class Home(APIView):

    serializer_class = UserSerializer
    permission_classes = ()
    # permission_classes = ([IsAuthenticated])

    def get(self,request):

        return Response({'API root':True},status=status.HTTP_202_ACCEPTED)

class DeleteUserView(APIView):

    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = (AllowAny,)

    def post(self,request):
        try:
            user_email  = str(request.data['email'])
            user = User.objects.get(email = user_email )
            securities = Security.objects.filter(user=user)
            for security in securities:
                security.delete()
            profiles = Profile.objects.filter(user=user)
            for profile in profiles:
                profile.delete()
            user.delete()            
            return Response({'deleted':True},status=status.HTTP_200_OK)

        except ObjectDoesNotExist:
            return Response({'message':'User does not exist'},status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'message':e},status=status.HTTP_400_BAD_REQUEST)
 

class DeleteAllUsersView(APIView):

    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = (AllowAny,)

    def post(self,request):
        try:
            users = User.objects.all()
            for user in users: 
                securities = Security.objects.filter(user=user)
                for security in securities:
                    security.delete()
                profiles = Profile.objects.filter(user=user)
                for profile in profiles:
                    profile.delete()
                user.delete()            
            return Response({'deleted':True},status=status.HTTP_200_OK)

        except ObjectDoesNotExist:
            return Response({'message':'User does not exist'},status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'message':e},status=status.HTTP_400_BAD_REQUEST)
 


class UserView(APIView):

    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = ([IsAuthenticated])

    def get(self,request):
        return Response({'data':getUserData(request),'status':'success'},status=status.HTTP_200_OK)
    
    def delete(self,request):
        user = request.user   
        profile = Profile.objects.get(user=user)
        profile_type = ''
        if profile.is_teacher:
            profile_type = 'teacher'
        elif profile.is_student:
            profile_type = 'student'
        elif profile.is_admin:
            profile_type = 'admin'
        
        deleted_user =  DeletedUser.objects.create(first_name = user.first_name,last_name = user.last_name,phone_number = user.phone_number,
                                                   profile_type=profile_type,email=user.email,date_of_birth=user.date_of_birth)
        # user.is_active = False
        # user.is_deleted = True
        user.delete()
        return Response({"status": "success"},status=status.HTTP_200_OK)


class UpdateProfileView(APIView):

    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = ([IsAuthenticated])

    def post(self,request):

        first_name = str(request.data['first_name'])
        last_name = str(request.data['last_name'])
        user = request.user
        user.first_name = first_name
        user.last_name = last_name
        user.save()
        serialized_user = UserSerializer(user).data
        data = {'status':'success','user':serialized_user}
        return Response(data,status=status.HTTP_200_OK)



class SetProfileView(APIView):

    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = ([IsAuthenticated])

    def post(self,request):
        
        profile_type = str(request.data['profile_type'])
        user = request.user
        try:
            user_profile = Profile.objects.get_or_create(user=user)[0]
        except Exception:
            return Response({'status':'error','eror':'User not found'},status=status.HTTP_400_BAD_REQUEST)

        if profile_type == 'admin':
            user_profile.is_admin = True
            user_profile.is_teacher = False
            user_profile.is_student = False
            print(user)
            Administrator.objects.get_or_create(user=user)
        elif profile_type == 'teacher':
            user_profile.is_teacher = True
            user_profile.is_admin = False
            user_profile.is_student = False
            Teacher.objects.get_or_create(user=user)
        elif profile_type == 'student':
            user_profile.is_admin = False
            user_profile.is_teacher = False
            user_profile.is_student = True
            Student.objects.get_or_create(user=user)
        response = {}
        user_profile.save()
        serialized_user = UserSerializer(user,many=False).data
        response['data'] = serialized_user
        response['data'].update(ProfileSerializer(user_profile,many=False).data)
        response['status'] = 'success'
        return Response(response,status=status.HTTP_200_OK)


class EditUserView(APIView):
    permission_classes = ([IsAuthenticated])
    queryset = User.objects.all()
    serializer_class = UserSerializer


class EditUserView(APIView):
    permission_classes = ([IsAuthenticated])
    queryset = User.objects.all()
    serializer_class = UserSerializer

    def put(self, request):
        try:
            first_name = str(request.data['first_name'])
            last_name = str(request.data['last_name'])
            user = request.user
            user.first_name = first_name
            user.last_name = last_name
            user.phone_number =  str(request.data['phone_number'])
            user.save()
            return Response({'user': getUserData(request), 'status': 'success'}, status=status.HTTP_202_ACCEPTED)
        except Exception as e:
            return Response({'status': 'error', 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class UploadAvatarView(APIView):
    permission_classes = ([IsAuthenticated])
    queryset = User.objects.all()
    serializer_class = UserSerializer

    def put(self,request):
        try:
            profile = User.objects.get(user=request.user)
            profile.profile_picture = request.FILES['profile_picture']
            profile.save()
            return Response({'user':getUserData(request),'status':'success'},status=status.HTTP_202_ACCEPTED)
        except Exception as e:
            return Response({'status':'error', 'message':str(e)},status=status.HTTP_400_BAD_REQUEST)






class Signup(APIView):

    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = (AllowAny,)
    authentication_classes=()

    def post(self,request):
        try:
            user = User.objects.get(email =str(request.data['email']).lower().strip(),is_deleted=False)
            return Response({'status':'error','message':'User with email already exist'},status=status.HTTP_400_BAD_REQUEST)
        except ObjectDoesNotExist:
            pass
        serializer = UserSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            while True:
                first_name = user.first_name or ""
                last_name = user.last_name or ""
                username = f'{first_name}{last_name}{round(random()*999999)}'.lower()
                try:
                    User.objects.get(username=username)
                except ObjectDoesNotExist:
                    user.username = username
                    user.save()
                    break
            user.email = str(user.email).lower().strip()
            # Handle user_type from request
            user_type = request.data.get('user_type', 'customer')
            if user_type in ['customer', 'designer']:
                user.user_type = user_type
            user.save()
            
            serialized_data = UserSerializer(user)
            send_verification_email(user)
            data = {'status':'success','data':serialized_data.data}
            return Response(data,status=status.HTTP_202_ACCEPTED)
        else:
            print(serializer.errors)
            return Response({'status':'error', 'message':str(serializer.error_messages)},status=status.HTTP_400_BAD_REQUEST)



class DeleteAccount(APIView):
    permission_classes = ([IsAuthenticated])

    def post(self, request):
        user = request.user   
        user.delete()
        return Response({"status": "success"},status=status.HTTP_200_OK)




from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.decorators import api_view, permission_classes
def get_tokens_for_user(user):
    refresh = RefreshToken.for_user(user)

    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }


@api_view(['POST'])
@permission_classes([AllowAny])
@psa()
def VerifySocialLogin(request, backend):
    # scheme = request.is_secure() and "https" or "http"
    # url=f'{requestUrl(request)}/oauth/convert-token/'
    token=request.data.get('access_token')

    user = request.backend.do_auth(token)
    print(user,user.first_name)


    if user:
        # new_user=User.objects.get(user=user)
        print(user)
        user,_=User.objects.get_or_create(
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            is_active=True

            )
        print(user)
        
        token=get_tokens_for_user(user)
        print(token)
        
        return Response(
            {
                'token': token,
                'user':UserSerializer(user,many=False).data
            },
            status=status.HTTP_200_OK,
            )
    else:
        return Response(
            {
                'errors': {
                    'token': 'Invalid token'
                    }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
