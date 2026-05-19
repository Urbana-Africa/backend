from rest_framework import serializers
from apps.authentication.models import DeletedUser, Security, User, VerificationCode

class UserSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            'email', 'password', 'id', 'first_name', 'gender', 'last_name',
            'username', 'is_active', 'phone_number', 'user_type', 'date_of_birth',
            'is_superuser', 'is_staff', 'date_joined', 'is_verified',
            'profile_picture', 'role', 'avatar', 'height', 'size',
        )
        extra_kwargs = {'password': {'write_only': True}}

    def get_role(self, obj):
        try:
            if obj.designer_profile:
                return 'designer'
        except Exception:
            pass
        return obj.user_type

    def get_avatar(self, obj):
        if obj.profile_picture:
            return obj.profile_picture.url
        return None

    def create(self, validated_data):
        user = User.objects.create(**validated_data)
        user.set_password(validated_data['password'].strip())
        user.save()
        return user

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        return representation
    

class SecuritySerializer(serializers.ModelSerializer):
    class Meta:
        model = Security
        fields = '__all__'

    
class UserTwoFactorSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('password',)


class DeletedUserSerializer(serializers.ModelSerializer):
    class Meta:
        model= DeletedUser
        fields='__all__'



class SecuritySerializer(serializers.ModelSerializer):
    class Meta:
        model = Security
        fields = '__all__'

    
class DeletedUserSerializer(serializers.ModelSerializer):
    class Meta:
        model= DeletedUser
        fields='__all__'



class VerificationSerializer(serializers.ModelSerializer):
    class Meta:
        model=VerificationCode
        fields='__all__'