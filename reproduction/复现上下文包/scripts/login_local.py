import os,time
from playwright.sync_api import sync_playwright
BASE=os.path.dirname(os.path.abspath(__file__))
with sync_playwright() as p:
    ctx=p.chromium.launch_persistent_context(os.path.join(BASE,'chrome_profile_1688'),executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=False,locale='zh-CN',viewport={'width':1360,'height':900})
    page=ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto('https://www.1688.com/',wait_until='domcontentloaded',timeout=60000)
    except Exception as e:
        print('Navigation:',type(e).__name__,flush=True)
    print('LOGIN_WINDOW_READY',flush=True)
    deadline=time.time()+600
    while time.time()<deadline:
        try:
            names={c['name'] for c in ctx.cookies()}
            if 'unb' in names and names.intersection({'_nk_','lgc','havana_lgc','sgcookie'}) and 'login.' not in page.url:
                ctx.storage_state(path=os.path.join(BASE,'1688_storage_state.json'))
                print('LOGIN_STATE_SAVED',flush=True)
                break
        except Exception:
            pass
        time.sleep(3)
    else:
        print('LOGIN_TIMEOUT',flush=True)
    ctx.close()
