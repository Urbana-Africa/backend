import json
from channels.generic.websocket import AsyncWebsocketConsumer

class SupportTicketConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.ticket_id = self.scope['url_route']['kwargs']['ticket_id']
        self.room_group_name = f'ticket_{self.ticket_id}'

        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    # Receive message from WebSocket (if client sends anything directly, but we mostly use HTTP for creates)
    async def receive(self, text_data):
        pass

    # Receive message from room group (sent by views.py)
    async def ticket_message(self, event):
        message = event['message']

        # Send message to WebSocket
        await self.send(text_data=json.dumps({
            'type': 'ticket_message',
            'message': message
        }))

    async def ticket_join(self, event):
        message = event['message']
        await self.send(text_data=json.dumps({
            'type': 'ticket_join',
            'message': message
        }))
