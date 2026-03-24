"""
Google App Password Auto-Generator - Selenium Automation Module
Automates Google login (with 2FA via 2fa.live) and App Password creation.
"""

import re
import time
import traceback
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager


def create_driver():
    """Create a Chrome WebDriver instance (non-headless for visibility)."""
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    prefs = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False
    }
    chrome_options.add_experimental_option("prefs", prefs)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    # Remove webdriver flag to avoid detection
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    return driver


def get_2fa_code(driver, secret_code, email, on_log=None):
    """
    Get 2FA code from https://2fa.live/ by entering the secret code.
    Opens a new tab, gets the code, then switches back to the original tab.

    Args:
        driver: Selenium WebDriver instance
        secret_code: The 2FA secret code
        email: Email address (for logging)
        on_log: Callback for logging

    Returns:
        The 2FA code string, or None if failed
    """
    def log(msg):
        if on_log:
            on_log(msg)
        print(msg)

    original_window = driver.current_window_handle
    # Record ALL existing window handles before opening a new tab
    # so we can correctly identify the new one (avoids "Sign in to Chrome?" popup handles)
    handles_before = set(driver.window_handles)

    try:
        log(f"[{email}] Opening 2fa.live to get 2FA code...")

        # Open new tab and navigate directly to 2fa.live
        driver.execute_script("window.open('https://2fa.live/', '_blank');")
        time.sleep(2)

        # Find the truly new tab by diffing handles
        handles_after = set(driver.window_handles)
        new_handles = handles_after - handles_before
        if not new_handles:
            log(f"[{email}] ⚠️ No new tab detected, trying last handle...")
            new_tab = driver.window_handles[-1]
        else:
            new_tab = new_handles.pop()

        driver.switch_to.window(new_tab)
        log(f"[{email}] Switched to 2fa.live tab (handle: {new_tab})")

        wait = WebDriverWait(driver, 15)
        time.sleep(2)

        # Enter secret code into the textarea
        log(f"[{email}] Entering secret code into 2fa.live...")
        token_input = wait.until(EC.visibility_of_element_located((By.ID, "listToken")))
        token_input.clear()
        token_input.send_keys(secret_code)
        time.sleep(1)

        # Click Submit button
        log(f"[{email}] Clicking Submit...")
        submit_btn = wait.until(EC.element_to_be_clickable((By.ID, "submit")))
        submit_btn.click()
        time.sleep(3)

        # Extract 2FA code from output textarea
        output_textarea = wait.until(EC.presence_of_element_located((By.ID, "output")))

        # Wait for the output to be populated (retry a few times)
        totp_code = None
        for attempt in range(10):
            output_text = output_textarea.get_attribute("value") or output_textarea.text
            log(f"[{email}] 2fa.live output: '{output_text}'")

            if output_text and "|" in output_text:
                # Format is: secretcode|2FACode
                parts = output_text.strip().split("|")
                if len(parts) >= 2:
                    code = parts[-1].strip()
                    # 2FA codes are typically 6 digits
                    if code.isdigit() and len(code) == 6:
                        totp_code = code
                        break
            time.sleep(1)

        if totp_code:
            log(f"[{email}] ✅ Got 2FA code: {totp_code}")
        else:
            log(f"[{email}] ❌ Could not extract 2FA code from 2fa.live")

        # Close the 2fa.live tab and switch back to original
        driver.close()
        driver.switch_to.window(original_window)

        return totp_code

    except Exception as e:
        log(f"[{email}] ❌ Error getting 2FA code: {str(e)}")
        traceback.print_exc()
        # Clean up: close ALL extra tabs and switch back to original
        try:
            for handle in driver.window_handles:
                if handle != original_window:
                    driver.switch_to.window(handle)
                    driver.close()
            driver.switch_to.window(original_window)
        except Exception:
            pass
        return None


def login_google(driver, email, password, recovery_email, secret_code, on_log=None):
    """
    Login to Google account with 2FA support.

    Args:
        driver: Selenium WebDriver instance
        email: Google email address
        password: Account password
        recovery_email: Recovery email for verification
        secret_code: 2FA secret code for TOTP generation
        on_log: Callback function for logging progress

    Returns:
        True if login successful, False otherwise
    """
    def log(msg):
        if on_log:
            on_log(msg)
        print(msg)

    try:
        # Step 1: Navigate to login page with retries
        email_input = None
        for nav_attempt in range(1, 4):
            log(f"[{email}] Navigating to Google login... (attempt {nav_attempt}/3)")
            driver.get("https://accounts.google.com/signin")
            time.sleep(2)

            try:
                wait = WebDriverWait(driver, 10)
                email_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[type="email"]')))
                break  # Found login page
            except Exception:
                current_url = driver.current_url
                log(f"[{email}] ⚠️ Login page not found (URL: {current_url}), retrying...")
                # Try clearing cookies/session and going to signout first
                try:
                    driver.get("https://accounts.google.com/Logout")
                    time.sleep(2)
                except Exception:
                    pass

        if not email_input:
            log(f"[{email}] ❌ Could not reach Google login page after 3 attempts")
            return False

        # Enter email
        log(f"[{email}] Entering email...")
        email_input.clear()
        email_input.send_keys(email)
        time.sleep(0.5)
        email_input.send_keys(Keys.ENTER)

        # Helper: dismiss passkey / "sign in faster" / other Google prompts
        def dismiss_google_prompts():
            dismissed = False

            # Check URL for passkey enrollment speedbump
            try:
                cur = driver.current_url
                if "speedbump/passkeyenrollment" in cur:
                    log(f"[{email}] Passkey enrollment page detected...")
            except Exception:
                pass

            # Method 1: Find buttons where the button text or inner span text matches
            try:
                dismiss_texts = ["Not now", "Không", "Skip", "Bỏ qua", "No thanks", "Không, cảm ơn"]
                for text in dismiss_texts:
                    # Direct button text match
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

            # Method 2: Try links/anchors with dismiss texts
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

            # Method 3: Google passkey dialog uses jsname="eBSUOb" for "Not now" button
            if not dismissed:
                try:
                    not_now_btn = driver.find_elements(By.CSS_SELECTOR, '[jsname="eBSUOb"] button')
                    for btn in not_now_btn:
                        if btn.is_displayed():
                            log(f"[{email}] Dismissing passkey prompt (jsname selector)...")
                            btn.click()
                            time.sleep(2)
                            dismissed = True
                            break
                except Exception:
                    pass

            # Check if redirected to the "about" page after dismissing
            # URL pattern: google.com/account/about (with any language param)
            try:
                cur = driver.current_url
                if "/account/about" in cur:
                    log(f"[{email}] ⚠️ Redirected to about page ({cur}), navigating back to login...")
                    driver.get("https://accounts.google.com/signin")
                    time.sleep(2)
            except Exception:
                pass

            # Check if redirected to the "about" page after dismissing
            # URL pattern: google.com/account/about (with any language param)
            try:
                cur = driver.current_url
                if "/account/about" in cur:
                    log(f"[{email}] ⚠️ Redirected to about page ({cur}), navigating back to login...")
                    driver.get("https://accounts.google.com/signin")
                    time.sleep(2)
            except Exception:
                pass

        # Step 2: Enter password
        log(f"[{email}] Entering password...")
        time.sleep(2)
        password_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[type="password"]')))
        password_input.clear()
        password_input.send_keys(password)
        time.sleep(0.5)
        password_input.send_keys(Keys.ENTER)

        # Step 3: Wait and check what Google asks for next
        time.sleep(3)

        # Dismiss passkey / "sign in faster" prompts if they appear
        dismiss_google_prompts()

        log(f"[{email}] Checking for 2FA prompt...")

        # Check if 2FA is required
        if not handle_2fa_challenge(driver, secret_code, email, on_log):
            return False

        # Dismiss any prompts after 2FA
        time.sleep(1)
        dismiss_google_prompts()

        # Step 4: Handle potential recovery email prompt
        time.sleep(2)
        log(f"[{email}] Checking for recovery email prompt...")
        try:
            recovery_options = driver.find_elements(By.XPATH, "//*[contains(text(), 'recovery email')]")
            if recovery_options:
                log(f"[{email}] Recovery email verification detected...")
                for option in recovery_options:
                    if option.is_displayed():
                        option.click()
                        time.sleep(2)
                        break

            recovery_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="email"]')
            for inp in recovery_inputs:
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

        # Dismiss any prompts after recovery email
        time.sleep(1)
        dismiss_google_prompts()

        # Step 5: Final login check
        time.sleep(2)
        current_url = driver.current_url
        log(f"[{email}] Current URL after login: {current_url}")

        # If landed on /account/about page, we may still be logged in
        # Just navigate to myaccount to verify
        if "/account/about" in current_url:
            log(f"[{email}] On about page, checking if session is active...")
            driver.get("https://myaccount.google.com/")
            time.sleep(3)
            if "myaccount.google.com" in driver.current_url:
                log(f"[{email}] ✅ Login successful!")
                return True
            # Not logged in — try signing in again
            log(f"[{email}] ⚠️ Session not active, retrying login...")
            driver.get("https://accounts.google.com/signin")
            time.sleep(2)

        if "myaccount.google.com" in current_url or "accounts.google.com/signin" not in current_url:
            driver.get("https://myaccount.google.com/")
            time.sleep(2)
            if "myaccount.google.com" in driver.current_url:
                log(f"[{email}] ✅ Login successful!")
                return True

        log(f"[{email}] ⚠️ Login may have additional challenges. Current URL: {driver.current_url}")
        time.sleep(3)

        # One more check
        dismiss_google_prompts()
        driver.get("https://myaccount.google.com/")
        time.sleep(3)
        if "myaccount.google.com" in driver.current_url:
            log(f"[{email}] ✅ Login successful!")
            return True

        log(f"[{email}] ❌ Login failed or requires manual intervention")
        return False

    except Exception as e:
        log(f"[{email}] ❌ Login error: {str(e)}")
        traceback.print_exc()
        return False


def handle_2fa_challenge(driver, secret_code, email, on_log=None):
    """
    Check for and handle a 2FA challenge on the current page.
    Used both during login and when accessing app passwords.

    Args:
        driver: Selenium WebDriver instance
        secret_code: The 2FA secret code
        email: Email (for logging)
        on_log: Callback for logging

    Returns:
        True if 2FA was handled (or not needed), False if failed
    """
    def log(msg):
        if on_log:
            on_log(msg)
        print(msg)

    totp_selectors = [
        'input[id="totpPin"]',
        'input[name="totpPin"]',
        'input[type="tel"]',
        '#idvPin',
        'input[name="pin"]',
    ]

    # First, check if Google is showing a 2FA method chooser screen
    # If so, click "Google Authenticator" option to get to the TOTP input
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

                max_retries = 3
                for attempt in range(1, max_retries + 1):
                    totp_code = get_2fa_code(driver, secret_code, email, on_log)
                    if not totp_code:
                        log(f"[{email}] ❌ Failed to get 2FA code (attempt {attempt}/{max_retries})")
                        if attempt < max_retries:
                            log(f"[{email}] ⏳ Waiting 5s before retry...")
                            time.sleep(5)
                            continue
                        return False

                    log(f"[{email}] Entering 2FA code: {totp_code} (attempt {attempt}/{max_retries})")
                    # Re-find input after tab switch
                    inp = driver.find_element(By.CSS_SELECTOR, selector)
                    inp.clear()
                    inp.send_keys(totp_code)
                    time.sleep(0.5)
                    inp.send_keys(Keys.ENTER)
                    time.sleep(3)

                    # Check if Google shows "Wrong code" error
                    try:
                        page_text = driver.find_element(By.TAG_NAME, "body").text
                        if "Wrong code" in page_text or "wrong code" in page_text.lower():
                            log(f"[{email}] ⚠️ Wrong code detected! Retrying...")
                            if attempt < max_retries:
                                # Wait for next TOTP cycle (codes rotate every 30s)
                                log(f"[{email}] ⏳ Waiting 10s for next TOTP cycle...")
                                time.sleep(10)
                                continue
                            else:
                                log(f"[{email}] ❌ All {max_retries} 2FA attempts failed")
                                return False
                    except Exception:
                        pass

                    # No error detected — success
                    return True

                return False

    # No 2FA prompt found — that's fine
    return True


def create_app_password(driver, email, secret_code, on_log=None):
    """
    Navigate to App Passwords page and create a new app password.
    Handles 2FA re-verification if prompted.

    Args:
        driver: Selenium WebDriver instance (must be logged in)
        email: Email address (for logging)
        secret_code: 2FA secret code (needed for re-verification)
        on_log: Callback for logging

    Returns:
        The generated app password string, or None if failed
    """
    def log(msg):
        if on_log:
            on_log(msg)
        print(msg)

    try:
        log(f"[{email}] Navigating to App Passwords page...")
        driver.get("https://myaccount.google.com/apppasswords")
        wait = WebDriverWait(driver, 15)
        time.sleep(3)

        current_url = driver.current_url
        log(f"[{email}] Current URL: {current_url}")

        # Check if Google is asking for 2FA re-verification
        if "signin" in current_url.lower() or "challenge" in current_url.lower():
            log(f"[{email}] Google requires 2FA re-verification for App Passwords...")

            # First check if it's asking for password again
            try:
                password_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="password"]')
                for pw_inp in password_inputs:
                    if pw_inp.is_displayed():
                        log(f"[{email}] Re-entering password...")
                        # We don't have the password here, but Google may just need 2FA
                        break
            except Exception:
                pass

            # Handle 2FA challenge
            fa_result = handle_2fa_challenge(driver, secret_code, email, on_log)
            if not fa_result:
                log(f"[{email}] ❌ 2FA re-verification failed")
                return None

            time.sleep(3)
            current_url = driver.current_url
            log(f"[{email}] URL after 2FA: {current_url}")

            # If still not on apppasswords page, navigate again
            if "apppasswords" not in current_url:
                driver.get("https://myaccount.google.com/apppasswords")
                time.sleep(3)

        # Enter app name
        log(f"[{email}] Entering app name...")
        try:
            app_name_input = wait.until(EC.visibility_of_element_located(
                (By.CSS_SELECTOR, 'input[aria-label="App name"], input[type="text"]')
            ))
            app_name_input.clear()
            app_name_input.send_keys("AAPW_AutoGen")
            time.sleep(1)
        except Exception:
            log(f"[{email}] Trying alternative input selector...")
            inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="text"]')
            found = False
            for inp in inputs:
                if inp.is_displayed():
                    inp.clear()
                    inp.send_keys("AAPW_AutoGen")
                    found = True
                    time.sleep(1)
                    break
            if not found:
                log(f"[{email}] ❌ Could not find app name input field")
                return None

        # Click Create button
        log(f"[{email}] Clicking Create button...")
        try:
            create_btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(text(), 'Create') or contains(text(), 'Tạo')]")
            ))
            create_btn.click()
        except Exception:
            buttons = driver.find_elements(By.TAG_NAME, "button")
            clicked = False
            for btn in buttons:
                btn_text = btn.text.strip().lower()
                if btn_text in ["create", "tạo", "generate"]:
                    btn.click()
                    clicked = True
                    break
            if not clicked:
                try:
                    app_name_input = driver.find_element(By.CSS_SELECTOR, 'input[type="text"]')
                    app_name_input.send_keys(Keys.ENTER)
                except Exception:
                    pass

        time.sleep(3)

        # Extract the generated app password
        log(f"[{email}] Extracting app password...")
        app_password = None

        # Method 1 (PRIMARY): Google shows password in a dialog with each letter
        # in a separate <span> inside <strong class="v2CTKd KaSAf"><div dir="ltr">
        try:
            # Target the specific dialog structure
            password_div = driver.find_elements(By.CSS_SELECTOR, 'strong.v2CTKd div[dir="ltr"]')
            if not password_div:
                password_div = driver.find_elements(By.CSS_SELECTOR, 'div[dir="ltr"]')

            for div in password_div:
                spans = div.find_elements(By.TAG_NAME, "span")
                if spans and len(spans) >= 16:
                    # Collect text from each span (each span = 1 letter)
                    chars = [s.text for s in spans]
                    raw = "".join(chars).strip()
                    # Validate: should be 16 alpha chars (possibly with spaces between groups)
                    clean = raw.replace(" ", "")
                    if len(clean) == 16 and clean.isalpha():
                        # Format as "xxxx xxxx xxxx xxxx"
                        app_password = f"{clean[0:4]} {clean[4:8]} {clean[8:12]} {clean[12:16]}"
                        log(f"[{email}] Extracted from dialog spans: {app_password}")
                        break
        except Exception as ex:
            log(f"[{email}] Method 1 (span extraction) failed: {str(ex)}")

        # Method 2: Regex search on page text for "xxxx xxxx xxxx xxxx" pattern
        if not app_password:
            try:
                page_text = driver.find_element(By.TAG_NAME, "body").text
                matches = re.findall(r'[a-z]{4}\s[a-z]{4}\s[a-z]{4}\s[a-z]{4}', page_text, re.IGNORECASE)
                if matches:
                    app_password = matches[0]
                    log(f"[{email}] Extracted via regex: {app_password}")
            except Exception:
                pass

        # Method 3: Get text from the dialog strong element directly
        if not app_password:
            try:
                strong_els = driver.find_elements(By.CSS_SELECTOR, 'strong.v2CTKd')
                for el in strong_els:
                    text = el.text.strip()
                    clean = text.replace(" ", "").replace("\n", "")
                    if len(clean) == 16 and clean.isalpha():
                        app_password = f"{clean[0:4]} {clean[4:8]} {clean[8:12]} {clean[12:16]}"
                        log(f"[{email}] Extracted from strong element: {app_password}")
                        break
            except Exception:
                pass

        if app_password:
            log(f"[{email}] ✅ App password generated: {app_password}")
            try:
                done_buttons = driver.find_elements(By.XPATH,
                    "//button[contains(text(), 'Done') or contains(text(), 'Xong') or contains(text(), 'OK')]")
                for btn in done_buttons:
                    if btn.is_displayed():
                        btn.click()
                        break
            except Exception:
                pass
            return app_password
        else:
            log(f"[{email}] ❌ Could not extract app password. Check browser window.")
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


def process_account(email, password, recovery_email, secret_code, on_log=None):
    """
    Process a single account: login (with 2FA) and create app password.

    Args:
        email: Google email
        password: Account password
        recovery_email: Recovery email
        secret_code: 2FA secret code
        on_log: Callback for logging

    Returns:
        dict with email, app_password, status
    """
    def log(msg):
        if on_log:
            on_log(msg)
        print(msg)

    driver = None
    result = {
        "email": email,
        "app_password": None,
        "status": "failed"
    }

    try:
        log(f"[{email}] Starting browser...")
        driver = create_driver()

        # Login with 2FA
        login_success = login_google(driver, email, password, recovery_email, secret_code, on_log)
        if not login_success:
            result["status"] = "login_failed"
            return result

        # Create app password
        app_password = create_app_password(driver, email, secret_code, on_log)
        if app_password:
            result["app_password"] = app_password
            result["status"] = "success"
        else:
            result["status"] = "app_password_failed"

    except Exception as e:
        log(f"[{email}] ❌ Unexpected error: {str(e)}")
        result["status"] = "error"
    finally:
        if driver:
            try:
                driver.quit()
                log(f"[{email}] Browser closed.")
            except Exception:
                pass

    return result


def process_accounts(accounts_text, on_log=None, on_result=None):
    """
    Process multiple accounts from text input.

    Args:
        accounts_text: String with accounts in format email|password|recoveryEmail|secretCode (one per line)
        on_log: Callback for logging messages
        on_result: Callback for each account result

    Returns:
        List of result dicts
    """
    def log(msg):
        if on_log:
            on_log(msg)
        print(msg)

    results = []
    lines = [line.strip() for line in accounts_text.strip().split("\n") if line.strip()]

    log(f"📋 Found {len(lines)} account(s) to process")

    for i, line in enumerate(lines, 1):
        parts = line.split("|")
        if len(parts) != 4:
            log(f"⚠️ Skipping invalid line {i}: '{line}' (expected format: email|password|recoveryEmail|secretCode)")
            results.append({
                "email": line,
                "app_password": None,
                "status": "invalid_format"
            })
            continue

        email = parts[0].strip()
        password = parts[1].strip()
        recovery_email = parts[2].strip()
        secret_code = parts[3].strip()

        log(f"\n{'='*60}")
        log(f"🔄 Processing account {i}/{len(lines)}: {email}")
        log(f"{'='*60}")

        result = process_account(email, password, recovery_email, secret_code, on_log)
        results.append(result)

        if on_result:
            on_result(result)

        # Brief pause between accounts
        if i < len(lines):
            log(f"⏳ Waiting 3 seconds before next account...")
            time.sleep(3)

    return results
