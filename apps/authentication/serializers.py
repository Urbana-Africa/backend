from rest_framework import serializers
from apps.authentication.models import DeletedUser, Security, User, VerificationCode

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('email', 'password','id','first_name','gender', 'last_name','username','is_active','phone_number','date_of_birth','is_superuser')
        extra_kwargs = {'password': {'write_only': True},}

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