import os
import logging
import threading
import time
import requests
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from automation import process_account

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AAPW] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("aapw.poller")

NIXUS_API_URL = os.environ.get("NIXUS_API_URL", "").rstrip("/")
AAPW_API_KEY = os.environ.get("AAPW_API_KEY", "")
POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "30"))

_poll_lock = threading.Lock()


def _headers():
    return {"x-aapw-api-key": AAPW_API_KEY, "Content-Type": "application/json"}


def fetch_pending_accounts():
    if not NIXUS_API_URL or not AAPW_API_KEY:
        log.error("NIXUS_API_URL or AAPW_API_KEY not set")
        return []
    try:
        r = requests.get(
            f"{NIXUS_API_URL}/api/aapw/accounts", headers=_headers(), timeout=15
        )
        r.raise_for_status()
        accounts = r.json().get("accounts", [])
        log.info(f"Fetched {len(accounts)} pending account(s) from nixus-labs")
        return accounts
    except Exception as e:
        log.error(f"Failed to fetch accounts: {e}")
        return []


def report_result(email_account_id, new_app_password, status, error=None):
    payload = {"emailAccountId": email_account_id, "status": status}
    if new_app_password:
        payload["newAppPassword"] = new_app_password
    if error:
        payload["error"] = error[:200]
    try:
        r = requests.post(
            f"{NIXUS_API_URL}/api/aapw/update",
            headers=_headers(),
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        log.info(f"Reported {status} for account {email_account_id}")
    except Exception as e:
        log.error(f"Failed to report result for {email_account_id}: {e}")


def run_poll():
    if not _poll_lock.acquire(blocking=False):
        log.info("Previous poll still running, skipping")
        return
    try:
        accounts = fetch_pending_accounts()
        if not accounts:
            return
        for acct in accounts:
            email_account_id = acct.get("emailAccountId")
            email = acct.get("email")
            google_password = acct.get("googlePassword")
            recovery_email = acct.get("recoveryEmail") or ""
            two_fa_secret = acct.get("twoFaSecret") or ""

            if not email_account_id or not email or not google_password:
                log.warning(f"Skipping incomplete account: {acct}")
                continue

            log.info(f"Processing {email} ...")
            try:
                result = process_account(
                    email,
                    google_password,
                    recovery_email,
                    two_fa_secret,
                    on_log=lambda m, _e=email: log.info(f"  [{_e}] {m}"),
                )
                if result.get("status") == "success":
                    log.info(f"  {email} -> SUCCESS")
                    report_result(
                        email_account_id, result.get("app_password"), "success"
                    )
                else:
                    err = result.get("error_detail") or result.get("status", "unknown")
                    log.warning(f"  {email} -> FAILED: {err}")
                    report_result(email_account_id, None, "failed", err)
            except Exception as e:
                log.error(f"  {email} -> EXCEPTION: {e}")
                report_result(email_account_id, None, "failed", str(e))
    finally:
        _poll_lock.release()


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_poll,
        trigger=IntervalTrigger(minutes=POLL_INTERVAL_MINUTES),
        id="aapw_poll",
        name="AAPW Poll",
        replace_existing=True,
        next_run_time=datetime.utcnow(),
    )
    scheduler.start()
    log.info(f"Scheduler started — polling every {POLL_INTERVAL_MINUTES} minute(s)")
    return scheduler


if __name__ == "__main__":
    log.info("AAPW Daemon starting")
    scheduler = start_scheduler()
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("AAPW Daemon stopped")
