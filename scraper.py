import os
import time
import json
import random
import zlib

from cloakbrowser import launch

UTILITY_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DOWNLOAD_DIR = os.path.join(UTILITY_DIR, 'screenshots')
JSON_PATH = os.path.join(UTILITY_DIR, "prompts.json")
PROXIES_PATH = os.path.join(UTILITY_DIR, "proxies.txt")

TIMEOUT_START = 60
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
    stable = 0
    for _ in range(60):
        time.sleep(0.5)
        if is_still_generating(page):
            prev_len = -1
            stable = 0
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
    """Attempts to click the banner to remove the overlay, but does not hide the UI."""
    try:
        btn = page.locator(
            "button:has-text('Reject'), "
            "button:has-text('Accept all'), "
            "button:has-text('Necessary only')"
        ).first
        if btn.is_visible(timeout=3000):
            btn.click()
            time.sleep(0.8)
    except Exception:
        pass

def run_prompt(page, prompt_text, output_path):
    # Set a standard desktop width, but a normal height to start so the UI renders normally
    page.set_viewport_size({"width": 1440, "height": 1080})

    # Navigate directly to the chat page and wait for network idle
    page.goto("https://chat.openai.com/chat", wait_until="networkidle", timeout=60000)
    dismiss_cookie_banner(page)

    # 1. THE LOGIN WALL DETECTOR
    time.sleep(3)

    try:
        input_visible = page.locator(
            'textarea:visible, [contenteditable="true"]:visible, #prompt-textarea:visible').count() > 0
        if not input_visible:
            for sel in [
                "button:has-text('Continue with Google')",
                "button:has-text('Continue with Apple')",
                "button:has-text('Sign in with Google')",
                "text=Continue with Google",
            ]:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible(timeout=2000):
                        raise Exception("Hit ChatGPT login wall — Anonymous chat blocked on this proxy.")
                except Exception:
                    pass
    except Exception as e:
        if "login wall" in str(e):
            raise e
        raise Exception("Could not determine chat input visibility; treating as blocked.")

    # 2. SUBMIT THE PROMPT
    input_box = page.locator(
        'textarea:visible, '
        '[contenteditable="true"]:visible, '
        '#prompt-textarea:visible'
    ).first

    input_box.wait_for(state="visible", timeout=15000)
    input_box.click(force=True)

    for ch in prompt_text:
        page.keyboard.type(ch)
        time.sleep(max(0.03, random.gauss(0.08, 0.03)))

    time.sleep(random.uniform(0.5, 1.2))
    page.keyboard.press("Enter")

    wait_for_response(page)

    if page.locator('[data-message-author-role="assistant"]').count() == 0:
        raise Exception("response element not found after generation")

    # 3. THE FULL UI CAPTURE FIX
    time.sleep(1.0)  # Let the UI settle completely
    dismiss_cookie_banner(page)  # Double check it didn't pop up again

    try:
        # Dynamically calculate how tall the chat container actually is
        chat_height = page.evaluate("""() => {
            const main = document.querySelector('main');
            return main ? main.scrollHeight : document.body.scrollHeight;
        }""")

        new_height = max(1080, int(chat_height) + 150)
        page.set_viewport_size({"width": 1440, "height": new_height})
        time.sleep(1.5)  # Give the browser a second to redraw the UI at the new height
    except Exception as e:
        print(f"  -> warning: could not dynamically resize viewport: {e}")

    # Take the screenshot of the newly expanded UI
    page.screenshot(path=output_path, full_page=True)

    # 4. DOM DATA EXTRACTION (The Foolproof Way)
    print("  -> extracting text and sources from the page...", end='', flush=True)
    try:
        # Run JavaScript directly on the page to scrape the final text and links
        extracted_data = page.evaluate('''() => {
            const result = {
                "parsed_response": "",
                "sources": []
            };

            // Grab all assistant messages, and pick the last one (the current response)
            const messages = document.querySelectorAll('[data-message-author-role="assistant"]');
            if (messages.length > 0) {
                const lastMessage = messages[messages.length - 1];

                // Get the plain text of the response
                result.parsed_response = lastMessage.innerText;

                // Grab all links inside the response to build our sources list
                const links = lastMessage.querySelectorAll('a');
                links.forEach(link => {
                    const url = link.href;
                    // Filter out internal OpenAI links (like UI buttons) so we only get external sources
                    if (url && url.startsWith('http') && !url.includes('chatgpt.com') && !url.includes('chat.openai.com')) {
                        result.sources.push({
                            "text": link.innerText.trim(),
                            "url": url
                        });
                    }
                });
            }
            return result;
        }''')

        # Add the prompt back into the dictionary
        extracted_data["prompt"] = prompt_text

        # Save it to a file
        json_output_path = output_path.replace('.png', '.json')
        with open(json_output_path, 'w', encoding='utf-8') as f:
            json.dump(extracted_data, f, indent=4)
        print(f" saved to {os.path.basename(json_output_path)}")

    except Exception as e:
        print(f"\n  -> Failed to extract JSON data from DOM: {e}")

    def handle_response(response):
        # Only look at the specific API endpoint that handles the chat generation
        if "backend-api" in response.url and "conversation" in response.url and response.request.method == "POST":
            try:
                # The response is a stream of text bytes
                body = response.body().decode('utf-8')

                # Split the stream by lines
                lines = body.split('\n')
                for line in lines:
                    if line.startswith('data: ') and '[DONE]' not in line:
                        # Strip the 'data: ' prefix so we are left with pure JSON
                        json_str = line[6:]
                        try:
                            chunk_data = json.loads(json_str)
                            extracted_data["raw_stream_chunks"].append(chunk_data)

                            # Basic extraction of the text (schema may vary)
                            try:
                                parts = chunk_data.get('message', {}).get('content', {}).get('parts', [])
                                if parts:
                                    extracted_data["parsed_response"] = parts[0]
                            except Exception:
                                pass
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                print(f"    [!] Error parsing network data: {e}")

    # Attach the listener to the page BEFORE we submit the prompt
    page.on("response", handle_response)

    # 3. SUBMIT THE PROMPT
    input_box = page.locator(
        'textarea:visible, '
        '[contenteditable="true"]:visible, '
        '#prompt-textarea:visible'
    ).first

    input_box.wait_for(state="visible", timeout=15000)
    input_box.click(force=True)

    for ch in prompt_text:
        page.keyboard.type(ch)
        time.sleep(max(0.03, random.gauss(0.08, 0.03)))

    time.sleep(random.uniform(0.5, 1.2))
    page.keyboard.press("Enter")

    wait_for_response(page)

    if page.locator('[data-message-author-role="assistant"]').count() == 0:
        raise Exception("response element not found after generation")

    # 4. THE FULL UI CAPTURE FIX
    time.sleep(1.0)  # Let the UI settle completely
    dismiss_cookie_banner(page)  # Double check it didn't pop up again

    try:
        # Dynamically calculate how tall the chat container actually is
        chat_height = page.evaluate("""() => {
            const main = document.querySelector('main');
            return main ? main.scrollHeight : document.body.scrollHeight;
        }""")

        new_height = max(1080, int(chat_height) + 150)
        page.set_viewport_size({"width": 1440, "height": new_height})
        time.sleep(1.5)  # Give the browser a second to redraw the UI at the new height
    except Exception as e:
        print(f"  -> warning: could not dynamically resize viewport: {e}")

    # Take the screenshot of the newly expanded UI
    page.screenshot(path=output_path, full_page=True)

    # 5. SAVE THE INTERCEPTED JSON DATA
    try:
        json_output_path = output_path.replace('.png', '.json')
        with open(json_output_path, 'w', encoding='utf-8') as f:
            json.dump(extracted_data, f, indent=4)
        print(f"  -> JSON data extracted and saved to {os.path.basename(json_output_path)}")
    except Exception as e:
        print(f"  -> Failed to save JSON data: {e}")


def process_queue():
    print("[SYSTEM] starting queue...")

    try:
        with open(JSON_PATH, 'r') as f:
            prompts = json.load(f).get('prompts', [])
        proxy_list = [None]
    except FileNotFoundError as e:
        print(f"[ERROR] missing file: {e}")
        return

    total = len(prompts)

    for index, item in enumerate(prompts, 1):
        file_name = item['fileName']
        folder_name = item['folder']
        prompt_text = item['prompt']

        output_dir = os.path.join(BASE_DOWNLOAD_DIR, folder_name)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{file_name}.png")

        if os.path.exists(output_path):
            print(f"[{index}/{total}] skipped: {file_name}")
            continue

        print(f"[{index}/{total}] processing: {file_name}...")

        succeeded = False
        for attempt in range(1, 4):
            proxy_line = random.choice(proxy_list)
            if proxy_line:
                host, port, user, password = proxy_line.split(':')
                proxy_config = {
                    "server": f"http://{host}:{port}",
                    "username": user,
                    "password": password,
                }
            else:
                proxy_config = None

            profile_parent = os.path.join(UTILITY_DIR, 'profiles')
            os.makedirs(profile_parent, exist_ok=True)
            if proxy_line:
                host_port = f"{host}_{port}"
            else:
                host_port = f"local_{index}_{attempt}"
            profile_dir = os.path.join(profile_parent, f"profile_{host_port}")
            os.makedirs(profile_dir, exist_ok=True)

            _seed = zlib.adler32(profile_dir.encode()) & 0xffffffff

            init_script = """
try {
  Object.defineProperty(navigator, 'webdriver', {get: () => false});
  Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
  Object.defineProperty(navigator, 'userAgent', {get: () => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36'});
  Object.defineProperty(navigator, 'vendor', {get: () => 'Google Inc.'});
  Object.defineProperty(navigator, 'product', {get: () => 'Gecko'});
  Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
  Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
  Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
  Object.defineProperty(navigator, 'appVersion', {get: () => '5.0 (Windows)'});
  window.chrome = window.chrome || { runtime: {} };
  navigator.plugins = [{ name: 'Chrome PDF Plugin' }];
  navigator.mimeTypes = [{ type: 'application/pdf' }];

  if (navigator.permissions && navigator.permissions.query) {
    const _origPerm = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) => {
      if (params && params.name === 'notifications') return Promise.resolve({ state: 'denied' });
      return _origPerm(params);
    };
  }

  (function(){
    const orig = HTMLCanvasElement.prototype.toDataURL;
    const seed = __SEED__;
    HTMLCanvasElement.prototype.toDataURL = function() {
      try { return orig.apply(this, arguments) + '?fp=' + seed; } catch(e) { return orig.apply(this, arguments); }
    };
  })();

  try { window.RTCPeerConnection = undefined; window.RTCPeerConnectionOrig = undefined; } catch(e){}
  try { navigator.mediaDevices = navigator.mediaDevices || {}; navigator.mediaDevices.enumerateDevices = async () => []; navigator.mediaDevices.getUserMedia = async () => { throw new Error('getUserMedia disabled'); }; } catch(e){}
  try { const tz = 'America/Los_Angeles'; const _orig = Intl.DateTimeFormat.prototype.resolvedOptions; Intl.DateTimeFormat.prototype.resolvedOptions = function() { const r = _orig.apply(this, arguments); r.timeZone = tz; return r; }; } catch(e){}
  try { Object.defineProperty(navigator, 'connection', { get: () => ({ effectiveType:'4g', downlink:10, rtt:50 }) }); } catch(e){}
} catch(e){}
"""
            init_script = init_script.replace("__SEED__", str(_seed))

            ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
            sec_ch_ua = '"Google Chrome";v="149", "Chromium";v="149", "Not A;Brand";v="24"'

            launch_kwargs = {"humanize": True, "headless": True}
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config
            launch_kwargs["args"] = ["--no-sandbox", "--disable-blink-features=AutomationControlled",
                                     "--disable-dev-shm-usage", "--hide-scrollbars"]

            launch_kwargs.pop('executable_path', None)

            try:
                browser = launch(**launch_kwargs)
            except Exception as e:
                print(f"  -> browser launch failed: {e}")
                continue

            try:
                page = browser.new_page()

                try:
                    page.add_init_script(init_script)
                except Exception:
                    pass

                try:
                    page.set_extra_http_headers({
                        "accept-language": "en-US,en;q=0.9",
                        "sec-ch-ua": sec_ch_ua,
                        "sec-ch-ua-platform": '"Windows"'
                    })
                except Exception:
                    pass

                run_prompt(page, prompt_text, output_path)
                print(f"  -> captured via {host_port}")
                succeeded = True
                break
            except Exception as e:
                ts = int(time.time())
                dbg = os.path.join(output_dir, f"ERROR_{file_name}_{ts}.png")
                try:
                    page.screenshot(path=dbg)
                    print(f"  -> debug screenshot saved: {dbg}")
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