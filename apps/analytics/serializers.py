from rest_framework import serializers
from .models import Event, EventSchema, Identity, Session, DailyMetric


class EventSchemaSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventSchema
        fields = '__all__'


class IdentitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Identity
        fields = '__all__'


class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Session
        fields = '__all__'


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = '__all__'


class DailyMetricSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyMetric
        fields = '__all__'
