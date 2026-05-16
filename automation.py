"""
Google App Password Auto-Generator - Selenium Automation Module
Automates Google login (with 2FA via 2fa.live) and App Password creation.
"""

import os
import json
import re
import time
import traceback
from datetime import datetime, timezone
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager

load_dotenv()


def create_driver():
    """Create a Chrome WebDriver instance (visible, no proxy)."""
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    })

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


def get_2fa_code(driver, secret_code, email, on_log=None):
    """
    Get 2FA code from https://2fa.live/ by entering the secret code.
    Opens a new tab, gets the code, then switches back to the original tab.
    """
    def log(msg):
        if on_log:
            on_log(msg)
        print(msg)

    original_window = driver.current_window_handle
    handles_before = set(driver.window_handles)

    try:
        log(f"[{email}] Opening 2fa.live to get 2FA code...")
        driver.execute_script("window.open('https://2fa.live/', '_blank');")
        time.sleep(2)

        handles_after = set(driver.window_handles)
        new_handles = handles_after - handles_before
        if not new_handles:
            log(f"[{email}] ⚠️ No new tab detected, trying last handle...")
            new_tab = driver.window_handles[-1]
        else:
            new_tab = new_handles.pop()

        driver.switch_to.window(new_tab)
        log(f"[{email}] Switched to 2fa.live tab")

        wait = WebDriverWait(driver, 15)
        time.sleep(2)

        token_input = wait.until(EC.visibility_of_element_located((By.ID, "listToken")))
        token_input.clear()
        token_input.send_keys(secret_code)
        time.sleep(1)

        submit_btn = wait.until(EC.element_to_be_clickable((By.ID, "submit")))
        submit_btn.click()
        time.sleep(3)

        output_textarea = wait.until(EC.presence_of_element_located((By.ID, "output")))

        totp_code = None
        for _ in range(10):
            output_text = output_textarea.get_attribute("value") or output_textarea.text
            log(f"[{email}] 2fa.live output: '{output_text}'")
            if output_text and "|" in output_text:
                parts = output_text.strip().split("|")
                if len(parts) >= 2:
                    code = parts[-1].strip()
                    if code.isdigit() and len(code) == 6:
                        totp_code = code
                        break
            time.sleep(1)

        if totp_code:
            log(f"[{email}] ✅ Got 2FA code: {totp_code}")
        else:
            log(f"[{email}] ❌ Could not extract 2FA code from 2fa.live")

        driver.close()
        driver.switch_to.window(original_window)
        return totp_code

    except Exception as e:
        log(f"[{email}] ❌ Error getting 2FA code: {str(e)}")
        traceback.print_exc()
        try:
            for handle in driver.window_handles:
                if handle != original_window:
                    driver.switch_to.window(handle)
                    driver.close()
            driver.switch_to.window(original_window)
        except Exception:
            pass
        return None


def handle_2fa_challenge(driver, secret_code, email, on_log=None):
    """
    Check for and handle a 2FA challenge on the current page.
    Returns True if handled (or not needed), False if failed.
    """
    def log(msg):
        if on_log:
            on_log(msg)
        print(msg)

    totp_selectors = [
        'input[id="totpPin"]',
        'input[name="totpPin"]',
        'input[type="tel"]',
        "#idvPin",
        'input[name="pin"]',
    ]

    try:
        auth_keywords = [
            "Google Authenticator",
            "Authenticator app",
            "authenticator app",
            "verification code",
            "Ứng dụng Authenticator",
        ]
        for keyword in auth_keywords:
            options = driver.find_elements(By.XPATH, f"//*[contains(text(), '{keyword}')]")
            for opt in options:
                if opt.is_displayed():
                    log(f"[{email}] Found 2FA method chooser, selecting '{keyword}'...")
                    opt.click()
                    time.sleep(3)
                    break
    except Exception:
        pass

    for selector in totp_selectors:
        totp_inputs = driver.find_elements(By.CSS_SELECTOR, selector)
        for inp in totp_inputs:
            if inp.is_displayed():
                log(f"[{email}] 2FA prompt detected! Getting code from 2fa.live...")

                for attempt in range(1, 4):
                    totp_code = get_2fa_code(driver, secret_code, email, on_log)
                    if not totp_code:
                        log(f"[{email}] ❌ Failed to get 2FA code (attempt {attempt}/3)")
                        if attempt < 3:
                            time.sleep(5)
                            continue
                        return False

                    log(f"[{email}] Entering 2FA code: {totp_code} (attempt {attempt}/3)")
                    inp = driver.find_element(By.CSS_SELECTOR, selector)
                    inp.clear()
                    inp.send_keys(totp_code)
                    time.sleep(0.5)
                    inp.send_keys(Keys.ENTER)
                    time.sleep(3)

                    try:
                        page_text = driver.find_element(By.TAG_NAME, "body").text
                        if "Wrong code" in page_text or "wrong code" in page_text.lower():
                            log(f"[{email}] ⚠️ Wrong code, retrying...")
                            if attempt < 3:
                                time.sleep(10)
                                continue
                            log(f"[{email}] ❌ All 2FA attempts failed")
                            return False
                    except Exception:
                        pass

                    return True

                return False

    return True  # No 2FA prompt — that's fine


def login_google(driver, email, password, recovery_email, secret_code, on_log=None):
    """Login to Google account with 2FA support."""
    def log(msg):
        if on_log:
            on_log(msg)
        print(msg)

    def dismiss_google_prompts():
        dismiss_texts = ["Not now", "Không", "Skip", "Bỏ qua", "No thanks", "Không, cảm ơn"]
        dismissed = False

        try:
            cur = driver.current_url
            if "speedbump/passkeyenrollment" in cur:
                log(f"[{email}] Passkey enrollment page detected...")
        except Exception:
            pass

        try:
            for text in dismiss_texts:
                btns = driver.find_elements(By.XPATH, f"//button[contains(., '{text}')]")
                for btn in btns:
                    if btn.is_displayed():
                        log(f"[{email}] Dismissing prompt (clicked '{text}')...")
                        btn.click()
                        time.sleep(2)
                        dismissed = True
                        break
                if dismissed:
                    break
        except Exception:
            pass

        if not dismissed:
            try:
                for text in dismiss_texts:
                    links = driver.find_elements(By.XPATH, f"//a[contains(., '{text}')]")
                    for link in links:
                        if link.is_displayed():
                            log(f"[{email}] Dismissing prompt link (clicked '{text}')...")
                            link.click()
                            time.sleep(2)
                            dismissed = True
                            break
                    if dismissed:
                        break
            except Exception:
                pass

        if not dismissed:
            try:
                for btn in driver.find_elements(By.CSS_SELECTOR, '[jsname="eBSUOb"] button'):
                    if btn.is_displayed():
                        log(f"[{email}] Dismissing passkey prompt...")
                        btn.click()
                        time.sleep(2)
                        break
            except Exception:
                pass

        try:
            if "/account/about" in driver.current_url:
                log(f"[{email}] ⚠️ Redirected to about page, navigating back...")
                driver.get("https://accounts.google.com/signin")
                time.sleep(2)
        except Exception:
            pass

    try:
        email_input = None
        for nav_attempt in range(1, 4):
            log(f"[{email}] Navigating to Google login... (attempt {nav_attempt}/3)")
            driver.get("https://accounts.google.com/signin")
            time.sleep(2)
            try:
                wait = WebDriverWait(driver, 10)
                email_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[type="email"]')))
                break
            except Exception:
                log(f"[{email}] ⚠️ Login page not found (URL: {driver.current_url}), retrying...")
                try:
                    driver.get("https://accounts.google.com/Logout")
                    time.sleep(2)
                except Exception:
                    pass

        if not email_input:
            log(f"[{email}] ❌ Could not reach Google login page after 3 attempts")
            return False

        log(f"[{email}] Entering email...")
        email_input.clear()
        email_input.send_keys(email)
        time.sleep(0.5)
        email_input.send_keys(Keys.ENTER)
        time.sleep(2)

        log(f"[{email}] Entering password...")
        wait = WebDriverWait(driver, 15)
        password_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[type="password"]')))
        password_input.clear()
        password_input.send_keys(password)
        time.sleep(0.5)
        password_input.send_keys(Keys.ENTER)
        time.sleep(3)

        dismiss_google_prompts()

        log(f"[{email}] Checking for 2FA prompt...")
        if not handle_2fa_challenge(driver, secret_code, email, on_log):
            return False

        time.sleep(1)
        dismiss_google_prompts()

        # Handle recovery email prompt
        time.sleep(2)
        log(f"[{email}] Checking for recovery email prompt...")
        try:
            recovery_options = driver.find_elements(By.XPATH, "//*[contains(text(), 'recovery email')]")
            if recovery_options:
                for option in recovery_options:
                    if option.is_displayed():
                        option.click()
                        time.sleep(2)
                        break

            for inp in driver.find_elements(By.CSS_SELECTOR, 'input[type="email"]'):
                if inp.is_displayed():
                    log(f"[{email}] Entering recovery email...")
                    inp.clear()
                    inp.send_keys(recovery_email)
                    time.sleep(0.5)
                    inp.send_keys(Keys.ENTER)
                    time.sleep(3)
                    break
        except Exception:
            pass

        time.sleep(1)
        dismiss_google_prompts()
        time.sleep(2)

        current_url = driver.current_url
        log(f"[{email}] Current URL after login: {current_url}")

        if "/account/about" in current_url:
            driver.get("https://myaccount.google.com/")
            time.sleep(3)
            if "myaccount.google.com" in driver.current_url:
                log(f"[{email}] ✅ Login successful!")
                return True
            driver.get("https://accounts.google.com/signin")
            time.sleep(2)

        if "myaccount.google.com" in current_url or "accounts.google.com/signin" not in current_url:
            driver.get("https://myaccount.google.com/")
            time.sleep(2)
            if "myaccount.google.com" in driver.current_url:
                log(f"[{email}] ✅ Login successful!")
                return True

        log(f"[{email}] ⚠️ Login may have additional challenges. URL: {driver.current_url}")
        time.sleep(3)
        dismiss_google_prompts()
        driver.get("https://myaccount.google.com/")
        time.sleep(3)
        if "myaccount.google.com" in driver.current_url:
            log(f"[{email}] ✅ Login successful!")
            return True

        log(f"[{email}] ❌ Login failed")
        return False

    except Exception as e:
        log(f"[{email}] ❌ Login error: {str(e)}")
        traceback.print_exc()
        return False


def is_valid_app_password(password):
    """Returns True if the string looks like a real Google App Password (xxxx xxxx xxxx xxxx)."""
    if not password or not isinstance(password, str):
        return False
    return bool(re.fullmatch(r"[a-z]{4} [a-z]{4} [a-z]{4} [a-z]{4}", password.strip()))


def create_app_password(driver, email, secret_code, on_log=None, max_attempts=3):
    """Navigate to App Passwords and create a new one. Retries up to max_attempts times."""
    def log(msg):
        if on_log:
            on_log(msg)
        print(msg)

    def extract_app_password():
        # Method 1: spans inside dialog div
        try:
            password_div = driver.find_elements(By.CSS_SELECTOR, 'strong.v2CTKd div[dir="ltr"]')
            if not password_div:
                password_div = driver.find_elements(By.CSS_SELECTOR, 'div[dir="ltr"]')
            for div in password_div:
                spans = div.find_elements(By.TAG_NAME, "span")
                if spans and len(spans) >= 16:
                    clean = "".join(s.text for s in spans).replace(" ", "")
                    if len(clean) == 16 and clean.isalpha():
                        return f"{clean[0:4]} {clean[4:8]} {clean[8:12]} {clean[12:16]}"
        except Exception:
            pass

        # Method 2: regex on page text
        try:
            matches = re.findall(r"[a-z]{4}\s[a-z]{4}\s[a-z]{4}\s[a-z]{4}",
                                 driver.find_element(By.TAG_NAME, "body").text, re.IGNORECASE)
            if matches:
                return matches[0]
        except Exception:
            pass

        # Method 3: strong element text
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, "strong.v2CTKd"):
                clean = el.text.strip().replace(" ", "").replace("\n", "")
                if len(clean) == 16 and clean.isalpha():
                    return f"{clean[0:4]} {clean[4:8]} {clean[8:12]} {clean[12:16]}"
        except Exception:
            pass

        return None

    def dismiss_dialog():
        try:
            for btn in driver.find_elements(By.XPATH,
                    "//button[contains(text(),'Done') or contains(text(),'Xong') "
                    "or contains(text(),'OK') or contains(text(),'Close') or contains(text(),'Đóng')]"):
                if btn.is_displayed():
                    btn.click()
                    time.sleep(2)
                    return
        except Exception:
            pass
        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(2)
        except Exception:
            pass

    try:
        log(f"[{email}] Navigating to App Passwords page...")
        driver.get("https://myaccount.google.com/apppasswords")
        wait = WebDriverWait(driver, 15)
        time.sleep(3)

        current_url = driver.current_url
        log(f"[{email}] Current URL: {current_url}")

        if "signin" in current_url.lower() or "challenge" in current_url.lower():
            log(f"[{email}] Google requires 2FA re-verification for App Passwords...")
            if not handle_2fa_challenge(driver, secret_code, email, on_log):
                log(f"[{email}] ❌ 2FA re-verification failed")
                return None
            time.sleep(3)
            if "apppasswords" not in driver.current_url:
                driver.get("https://myaccount.google.com/apppasswords")
                time.sleep(3)

        for attempt in range(1, max_attempts + 1):
            log(f"[{email}] App password creation attempt {attempt}/{max_attempts}...")

            # Enter app name
            try:
                app_name_input = wait.until(EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, 'input[aria-label="App name"], input[type="text"]')
                ))
                app_name_input.clear()
                app_name_input.send_keys(f"AAPW_AutoGen_{attempt}")
                time.sleep(1)
            except Exception:
                found = False
                for inp in driver.find_elements(By.CSS_SELECTOR, 'input[type="text"]'):
                    if inp.is_displayed():
                        inp.clear()
                        inp.send_keys(f"AAPW_AutoGen_{attempt}")
                        found = True
                        time.sleep(1)
                        break
                if not found:
                    log(f"[{email}] ❌ Could not find app name input")
                    if attempt < max_attempts:
                        driver.get("https://myaccount.google.com/apppasswords")
                        time.sleep(3)
                        continue
                    return None

            # Click Create
            try:
                create_btn = wait.until(EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(text(),'Create') or contains(text(),'Tạo')]")
                ))
                create_btn.click()
            except Exception:
                clicked = False
                for btn in driver.find_elements(By.TAG_NAME, "button"):
                    if btn.text.strip().lower() in ["create", "tạo", "generate"]:
                        btn.click()
                        clicked = True
                        break
                if not clicked:
                    try:
                        driver.find_element(By.CSS_SELECTOR, 'input[type="text"]').send_keys(Keys.ENTER)
                    except Exception:
                        pass

            time.sleep(3)

            app_password = extract_app_password()
            if app_password and is_valid_app_password(app_password):
                log(f"[{email}] ✅ App password generated: {app_password}")
                dismiss_dialog()
                return app_password
            elif app_password:
                log(f"[{email}] ⚠️ Invalid password extracted: '{app_password}' — retrying...")
            else:
                log(f"[{email}] ⚠️ Could not extract app password on attempt {attempt}")

            dismiss_dialog()
            if attempt < max_attempts:
                driver.get("https://myaccount.google.com/apppasswords")
                time.sleep(3)

        log(f"[{email}] ❌ Failed after {max_attempts} attempts")
        try:
            driver.save_screenshot("debug_screenshot.png")
            log(f"[{email}] Debug screenshot saved.")
        except Exception:
            pass
        return None

    except Exception as e:
        log(f"[{email}] ❌ Error creating app password: {str(e)}")
        traceback.print_exc()
        return None


# ── Persistence ──────────────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RESULTS_FILE = os.path.join(DATA_DIR, "results.json")
RUNS_DIR = os.path.join(DATA_DIR, "runs")


def save_result(result):
    os.makedirs(DATA_DIR, exist_ok=True)
    results = []
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                results = json.load(f)
        except (json.JSONDecodeError, IOError):
            results = []
    results.append(result)
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


def save_run(run_id, results):
    os.makedirs(RUNS_DIR, exist_ok=True)
    run_data = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "success": sum(1 for r in results if r.get("status") == "success"),
        "failed": sum(1 for r in results if r.get("status") != "success"),
        "results": results,
    }
    with open(os.path.join(RUNS_DIR, f"{run_id}.json"), "w", encoding="utf-8") as f:
        json.dump(run_data, f, indent=2, ensure_ascii=False)


# ── Main processing ───────────────────────────────────────────────────────────

def process_account(email, password, recovery_email, secret_code, on_log=None):
    """
    Process a single account: login (with 2FA) and create app password.
    Returns dict with email, app_password, status, timestamp, error_detail.
    """
    def log(msg):
        if on_log:
            on_log(msg)
        print(msg)

    result = {
        "email": email,
        "app_password": None,
        "status": "failed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error_detail": None,
    }

    driver = None
    try:
        log(f"[{email}] Starting browser...")
        driver = create_driver()

        if not login_google(driver, email, password, recovery_email, secret_code, on_log):
            result["status"] = "login_failed"
            result["error_detail"] = "Could not log in to Google account"
            save_result(result)
            return result

        app_password = create_app_password(driver, email, secret_code, on_log)
        if app_password:
            result["app_password"] = app_password
            result["status"] = "success"
        else:
            result["status"] = "app_password_failed"
            result["error_detail"] = "Logged in but could not generate app password"

    except Exception as e:
        log(f"[{email}] ❌ Unexpected error: {str(e)}")
        result["status"] = "error"
        result["error_detail"] = str(e)
    finally:
        if driver:
            try:
                driver.quit()
                log(f"[{email}] Browser closed.")
            except Exception:
                pass

    save_result(result)
    return result


def process_accounts(accounts_text, on_log=None, on_result=None):
    """
    Process multiple accounts from text.
    Format: email|password|recoveryEmail|secretCode (one per line, extra fields ignored).
    """
    def log(msg):
        if on_log:
            on_log(msg)
        print(msg)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results = []
    lines = [line.strip() for line in accounts_text.strip().split("\n") if line.strip()]

    log(f"📋 Found {len(lines)} account(s) to process (run: {run_id})")

    for i, line in enumerate(lines, 1):
        parts = line.split("|")
        if len(parts) < 4:
            log(f"⚠️ Skipping line {i}: '{line}' (need at least 4 pipe-separated fields)")
            invalid = {
                "email": parts[0].strip() if parts else line,
                "app_password": None,
                "status": "invalid_format",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error_detail": f"Got {len(parts)} fields, need 4",
            }
            results.append(invalid)
            save_result(invalid)
            if on_result:
                on_result(invalid)
            continue

        email = parts[0].strip()
        password = parts[1].strip()
        recovery_email = parts[2].strip()
        secret_code = parts[3].strip()

        log(f"\n{'=' * 60}")
        log(f"🔄 Processing account {i}/{len(lines)}: {email}")
        log(f"{'=' * 60}")

        result = process_account(email, password, recovery_email, secret_code, on_log)
        results.append(result)

        if on_result:
            on_result(result)

        if i < len(lines):
            log("⏳ Waiting 3 seconds before next account...")
            time.sleep(3)

    save_run(run_id, results)
    log(f"💾 Run {run_id} saved.")
    return results
