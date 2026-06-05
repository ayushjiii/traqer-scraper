import os
import time
import json
import random

from cloakbrowser import launch

UTILITY_DIR       = os.path.dirname(os.path.abspath(__file__))
BASE_DOWNLOAD_DIR = os.path.join(UTILITY_DIR, 'screenshots')
JSON_PATH         = os.path.join(UTILITY_DIR, "prompts.json")
PROXIES_PATH      = os.path.join(UTILITY_DIR, "proxies.txt")

TIMEOUT_START  = 60
TIMEOUT_FINISH = 300


def is_still_generating(page):
    try:
        return page.locator(
            'button[aria-label="Stop generating"], '
            '.result-streaming, '
            '[data-testid*="stop"]'
        ).count() > 0
    except Exception:
        return False


def wait_for_response(page):
    print("  -> waiting for response to start...", end='', flush=True)

    started = False
    deadline = time.time() + TIMEOUT_START
    while time.time() < deadline:
        try:
            has_activity = page.locator(
                'button[aria-label="Stop generating"], '
                '.result-streaming, '
                '[data-testid*="stop"], '
                '[data-message-author-role="assistant"]'
            ).count() > 0
            if has_activity:
                started = True
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not started:
        print()
        raise Exception(f"generation never started within {TIMEOUT_START}s")

    print(" started.")
    print("  -> waiting for response to finish...", end='', flush=True)

    deadline = time.time() + TIMEOUT_FINISH
    while time.time() < deadline:
        if not is_still_generating(page):
            break
        time.sleep(1)
    else:
        print()
        raise Exception(f"response never finished within {TIMEOUT_FINISH}s")

    print(" done.")
    print("  -> confirming stable...", end='', flush=True)

    prev_len = -1
    stable   = 0
    for _ in range(60):
        time.sleep(0.5)
        if is_still_generating(page):
            prev_len = -1
            stable   = 0
            continue
        try:
            msgs = page.locator('[data-message-author-role="assistant"]').all()
            cur_len = sum(len(m.inner_text()) for m in msgs)
        except Exception:
            cur_len = prev_len
        if cur_len == prev_len:
            stable += 1
            if stable >= 5:
                break
        else:
            stable = 0
        prev_len = cur_len

    print(" stable.")
    time.sleep(1.5)


def dismiss_cookie_banner(page):
    try:
        btn = page.locator(
            "button:has-text('Reject'), "
            "button:has-text('Accept all'), "
            "button:has-text('Necessary only')"
        ).first
        if btn.is_visible(timeout=5000):
            btn.click()
            time.sleep(0.8)
            print("  -> cookie banner dismissed")
    except Exception:
        pass


def collapse_sidebar(page):
    try:
        btn = page.locator(
            'button[aria-label="Close sidebar"], '
            'button[aria-label="Collapse sidebar"], '
            'button[data-testid="close-sidebar-button"]'
        ).first
        if btn.is_visible(timeout=3000):
            btn.click()
            time.sleep(0.6)
    except Exception:
        try:
            page.evaluate("const nav = document.querySelector('nav'); if (nav) nav.style.display = 'none';")
        except Exception:
            pass


def run_prompt(page, prompt_text, output_path):
    page.goto("https://chatgpt.com", wait_until="domcontentloaded", timeout=30000)
    dismiss_cookie_banner(page)
    collapse_sidebar(page)

    input_box = page.locator("#prompt-textarea")
    input_box.wait_for(state="visible", timeout=20000)
    input_box.click()

    # type with humanlike delay
    for ch in prompt_text:
        page.keyboard.type(ch)
        time.sleep(max(0.03, random.gauss(0.08, 0.03)))

    time.sleep(random.uniform(0.5, 1.2))
    page.keyboard.press("Enter")

    wait_for_response(page)

    if page.locator('[data-message-author-role="assistant"]').count() == 0:
        raise Exception("response element not found after generation")

    # Playwright full-page screenshot — no scroll-and-stitch needed
    page.screenshot(path=output_path, full_page=True)


def process_queue():
    print("[SYSTEM] starting queue...")

    try:
        with open(JSON_PATH, 'r') as f:
            prompts = json.load(f).get('prompts', [])
        with open(PROXIES_PATH, 'r') as f:
            proxy_list = [l.strip() for l in f.readlines() if l.strip()]
    except FileNotFoundError as e:
        print(f"[ERROR] missing file: {e}")
        return

    total = len(prompts)

    for index, item in enumerate(prompts, 1):
        file_name   = item['fileName']
        folder_name = item['folder']
        prompt_text = item['prompt']

        output_dir  = os.path.join(BASE_DOWNLOAD_DIR, folder_name)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{file_name}.png")

        if os.path.exists(output_path):
            print(f"[{index}/{total}] skipped: {file_name}")
            continue

        print(f"[{index}/{total}] processing: {file_name}...")

        succeeded = False
        for attempt in range(1, 4):
            proxy_line = random.choice(proxy_list)
            host, port, user, password = proxy_line.split(':')
            proxy_config = {
                "server":   f"http://{host}:{port}",
                "username": user,
                "password": password,
            }

            browser = launch(proxy=proxy_config, humanize=True)
            try:
                page = browser.new_page()
                run_prompt(page, prompt_text, output_path)
                print(f"  -> captured via {host}")
                succeeded = True
                break
            except Exception as e:
                ts  = int(time.time())
                dbg = os.path.join(output_dir, f"ERROR_{file_name}_{ts}.png")
                try:
                    page.screenshot(path=dbg)
                    print(f"  -> debug screenshot saved")
                except Exception:
                    print("  -> could not save debug screenshot")
                print(f"  -> attempt {attempt} failed: {e}")
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

        if not succeeded:
            print(f"  -> all attempts failed for {file_name}, skipping")

        if index < total:
            time.sleep(random.uniform(8.0, 14.0))

    print("\n[SYSTEM] batch complete")


if __name__ == "__main__":
    process_queue()
