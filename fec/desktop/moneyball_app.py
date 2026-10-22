"""Native Windows shell for the locally served Moneyball dashboard.

Uses WebView2, retains its own window/profile, and never shuts down the shared
data service or collection workers when the application window closes.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser


TITLE = 'Moneyball · Melange Systems'
SCHEMAS = {'melange-staffer-live-v1', 'melange-staffer-live-v2'}


def load_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    root = Path(config['source_root']).resolve()
    python = Path(config['service_python']).resolve()
    port = config.get('port', 8841)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid local service port')
    if not (root / 'fec/surfaces/staffer_live_server.py').is_file() or not python.is_file():
        raise ValueError('The configured Moneyball service is missing')
    if config.get('dashboard_file') not in {'Moneyball.html', 'Moneyball_Redesign.html'}:
        raise ValueError('Unsupported dashboard artifact')
    return {**config, 'source_root': str(root), 'service_python': str(python), 'port': port}


def health(port, timeout=5):
    # Avoid environment proxy settings for the loopback-only application service.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:{port}/api/staffer-live?limit=1', timeout=timeout) as response:
        result = json.load(response)
    if result.get('schema') not in SCHEMAS:
        raise ValueError('The port is not serving Moneyball')
    return result


def ensure_service(config, log_directory):
    port = config['port']
    with socket.socket() as probe:
        probe.settimeout(1)
        occupied = probe.connect_ex(('127.0.0.1', port)) == 0
    if occupied:
        health(port, timeout=45)
        return 'REUSED_EXISTING_SERVICE'
    script = Path(config['source_root']) / 'fec/surfaces/start_staffer_live.ps1'
    if not script.is_file():
        raise ValueError('The Moneyball service launcher is missing')
    env = dict(os.environ)
    # The established launcher resolves python through PATH.
    env['PATH'] = str(Path(config['service_python']).parent) + os.pathsep + env.get('PATH', '')
    command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
               '-SourceRoot', config['source_root'], '-Port', str(port),
               '-DashboardFile', config['dashboard_file']]
    with (log_directory / 'service-launch.log').open('a', encoding='utf-8') as log:
        result = subprocess.run(command, stdout=log, stderr=log, env=env,
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=90)
    if result.returncode:
        raise RuntimeError('The local service could not start; see service-launch.log')
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        try:
            health(port)
            return 'STARTED_LOCAL_SERVICE'
        except (OSError, ValueError):
            time.sleep(1)
    raise TimeoutError('The local service did not respond before the startup timeout')


def acquire_instance():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel.CreateMutexW(None, False, 'Local\\Melange.Moneyball.Desktop')
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user.FindWindowW.restype = wintypes.HWND
        window = user.FindWindowW(None, TITLE)
        if window:
            user.ShowWindow(window, 9)
            user.SetForegroundWindow(window)
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        return None
    return handle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    parser.add_argument('--config', type=Path, default=base / 'moneyball-app.json')
    parser.add_argument('--smoke-test', type=Path)
    args = parser.parse_args()
    state = Path(os.environ['LOCALAPPDATA']) / 'Melange/MoneyballApp/user'
    state.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=state / 'application.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    config = load_config(args.config)
    instance = acquire_instance()
    if not instance:
        return
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Melange.Moneyball.Desktop')
    import webview
    from webview.menu import Menu, MenuAction, MenuSeparator
    url = f"http://127.0.0.1:{config['port']}/"
    window = webview.create_window(TITLE, html='<html><body style="font:16px Segoe UI;padding:40px;background:#f7f4ef;color:#292622"><h1>Moneyball</h1><p id="message">Opening your research workspace…</p></body></html>',
                                   width=1440, height=960, min_size=(980, 680),
                                   background_color='#f7f4ef', text_select=True)
    webview.settings['ALLOW_DOWNLOADS'] = True
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
    webview.settings['ALLOW_FILE_URLS'] = False

    def start():
        try:
            mode = ensure_service(config, state)
            logging.info('Service ready: %s', mode)
            window.load_url(url)
            if args.smoke_test:
                deadline = time.monotonic() + 120
                evidence = {}
                while time.monotonic() < deadline:
                    time.sleep(2)
                    evidence = window.evaluate_js('JSON.stringify({title:document.title,url:location.href,sidebar:!!document.getElementById("melange-nav"),peopleLink:!!document.querySelector("#melange-nav a[href*=staffer]"),views:document.querySelectorAll("[id^=app-]").length})')
                    evidence = json.loads(evidence) if isinstance(evidence, str) else evidence
                    evidence = evidence or {}
                    if evidence and evidence.get('sidebar') and evidence.get('peopleLink'):
                        break
                result = {'status': 'PASS' if evidence.get('sidebar') and evidence.get('peopleLink') else 'FAIL',
                          'service': mode, 'renderer': 'WebView2', 'dom': evidence}
                args.smoke_test.parent.mkdir(parents=True, exist_ok=True)
                args.smoke_test.write_text(json.dumps(result, indent=2), encoding='utf-8')
                window.destroy()
        except Exception as exc:
            logging.exception('Application startup failed')
            message = 'Moneyball could not open. ' + str(exc) + ' Logs: ' + str(state)
            try:
                window.evaluate_js('var m=document.getElementById("message");if(m)m.textContent=' + json.dumps(message))
            except Exception:
                logging.exception('Could not show startup message')
            if args.smoke_test:
                try:
                    args.smoke_test.parent.mkdir(parents=True, exist_ok=True)
                    args.smoke_test.write_text(json.dumps({'status': 'FAIL', 'error': str(exc)}), encoding='utf-8')
                finally:
                    window.destroy()

    menu = [Menu('Workspace', [MenuAction('Home', lambda: window.load_url(url)),
                              MenuAction('Reload', lambda: window.evaluate_js('location.reload()')),
                              MenuAction('Open in browser', lambda: webbrowser.open(url)),
                              MenuSeparator(), MenuAction('Exit', window.destroy)])]
    webview.start(start, gui='edgechromium', private_mode=False,
                  storage_path=str(state / 'profile'), menu=menu)
    ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    ctypes.windll.kernel32.CloseHandle(instance)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        logging.exception('Moneyball could not start')
        ctypes.windll.user32.MessageBoxW(None, str(exc), 'Moneyball startup error', 0x10)
        sys.exit(1)
