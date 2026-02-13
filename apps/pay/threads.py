import threading, requests


class TransferThread(threading.Thread):
    def __init__(self, transfer):
        self.transfer = transfer
        threading.Thread.__init__(self)

    def run(self):
        self.transfer.transfer()

class DeleteUserThread(threading.Thread):
    def __init__(self, user_id):
        self.user_id = user_id
        threading.Thread.__init__(self)

    def run(self) -> None:
        # secret_key = AppModel.objects.get(app_name = "Algoridm Accounts").secret
        payload = {
            # "secret_key":secret_key,
            "user_id":str(self.user_id)
        }
        
        if env("URL_DEBUG") == "1":
            url_list = ["http://localhost:8000/accounts/delete_user/"]
        elif env("URL_DEBUG") == "0":
            url_list = [
                "https://algoridm-academy.herokuapp.com/accounts/delete_user/",
                "https://algoridm-food.herokuapp.com/accounts/delete_user/",
                "https://algoridm-logistics.herokuapp.com/accounts/delete_user/",
                
                        ]
            
        for url in url_list:
            requests.post(url, data=payload)
