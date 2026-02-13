from django.urls import path,re_path
from . import views as authviews
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)




urlpatterns = [
    path('', authviews.Home.as_view(),name='home'),
    path('user', authviews.UserView.as_view(),name='user'),
    path('update', authviews.UpdateProfileView.as_view(),name='users'),
    path('set-profile', authviews.SetProfileView.as_view(),name='set_profile'),
    path('upload-avatar', authviews.UploadAvatarView.as_view(),name='upload_avatar'),
    path('users', authviews.AllUserView.as_view(),name='users'),
    path('signup', authviews.Signup.as_view(),name='signup'),
    path('user/delete/all', authviews.DeleteAllUsersView.as_view(),name='delete_all_user'),
    path('user/delete', authviews.DeleteUserView.as_view(),name='delete_user'),
    path('user/edit', authviews.EditUserView.as_view(),name='edit_user'),
    path("login", authviews.LoginView.as_view(), name="login"),
    path("check-email", authviews.CheckEmail.as_view(), name="checkemail"),
    path("email/set", authviews.SetEmail.as_view(), name="setemail"),
    path("code/request", authviews.RequestVericationCodeView.as_view(), name="request_verification_code"),
    path("set-password", authviews.SetPassword.as_view(), name="set_password"),
    path("change-password", authviews.ChangePassword.as_view(), name="change_password"),
    path("confirm-code", authviews.VerifyCode.as_view(), name="confirm-code"),
    path("verify-email", authviews.VerifyEmail.as_view(), name="confirm_email"),
    path("resend-code", authviews.ResendVerificationCode.as_view(), name="resend_verification_code"),
    path('logout',authviews.Logout.as_view(),name='logout'),
    path('delete-account',authviews.DeleteAccount.as_view(),name='delete_account'),
    path('check-username',authviews.CheckUsername.as_view(),name='check_username'),
    path('change-username',authviews.ChangeUsername.as_view(),name='change_username'),
    path('token', authviews.CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh', authviews.CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('set-csrf', authviews.SetCSRFCookie.as_view(), name='set-csrf'),
    re_path('verify/'+ r'social/(?P<backend>[^/]+)/$',authviews.VerifySocialLogin,name='verify_social'),

]

