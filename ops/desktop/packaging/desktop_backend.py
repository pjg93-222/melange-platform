"""Native desktop adapter. Existing service source is unchanged and hosted mode is never weakened."""
from __future__ import annotations
import argparse
import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import secrets
import sys

TOKEN_HEADER = 'X-Desktop-Token'
HEALTH_PATH = '/__desktop/health'
ACTIVITY_PATH = '/__desktop/activity'
_process_job = None


def contain_windows_children():
    """The OS terminates this backend's children if the owning process exits."""
    global _process_job
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ['ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount']]
    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', BASIC_LIMIT), ('IoInfo', IO_COUNTERS),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    handle = kernel.CreateJobObjectW(None, None)
    limits = EXTENDED_LIMIT()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    # Non-inheritable handle closes at process exit; children cannot keep it alive.
    _process_job = handle


def valid_token(value, token):
    return isinstance(value, str) and hmac.compare_digest(value, token)


def guarded_handler(handler, token, app_id):
    """Authenticate before the existing handler routes any HTTP verb or returns app data."""
    class DesktopHandler(handler):
        def parse_request(self):
            if not super().parse_request():
                return False
            if not valid_token(self.headers.get(TOKEN_HEADER), token):
                self.send_error(403, 'Desktop authorization required')
                self.close_connection = True
                return False
            if self.path in {HEALTH_PATH, ACTIVITY_PATH} and self.command == 'GET':
                payload = {'appId': app_id, 'pid': os.getpid()}
                if self.path == ACTIVITY_PATH:
                    app = getattr(self.server, 'app', None)
                    payload = {'appId': app_id, 'busy': bool(app.busy()) if app_id == 'mentat' and app is not None and callable(getattr(app, 'busy', None)) else None}
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return False
            return True
    return DesktopHandler


class GuardedWSGI:
    def __init__(self, application, token, app_id, audit=None, launch_path=None, desktop_boundary=None):
        self.application, self.token, self.app_id = application, token, app_id
        self.audit = audit
        self.launch_path = launch_path
        self.desktop_boundary = desktop_boundary

    def __call__(self, environment, start_response):
        original_start = start_response
        def start_response(status, headers, exc_info=None):
            headers = [(key, value) for key, value in headers if key.lower() != 'referrer-policy']
            headers = [*headers, ('Referrer-Policy', 'no-referrer')]
            return original_start(status, headers, exc_info) if exc_info else original_start(status, headers)
        if not valid_token(environment.get('HTTP_X_DESKTOP_TOKEN'), self.token):
            start_response('403 Forbidden', [('Content-Type', 'text/plain'), ('Cache-Control', 'no-store')])
            return [b'Desktop authorization required']
        if self.desktop_boundary is not None and not self.desktop_boundary.authorize_environment(environment):
            start_response('403 Forbidden', [('Content-Type', 'text/plain'), ('Cache-Control', 'no-store')])
            return [b'Desktop request boundary refused']
        if environment.get('PATH_INFO') in {HEALTH_PATH, ACTIVITY_PATH} and environment.get('REQUEST_METHOD') == 'GET':
            payload = {'appId': self.app_id, 'pid': os.getpid()} if environment.get('PATH_INFO') == HEALTH_PATH else {'appId': self.app_id, 'busy': None}
            if environment.get('PATH_INFO') == HEALTH_PATH and self.launch_path:
                payload['launchPath'] = self.launch_path
            body = json.dumps(payload).encode()
            start_response('200 OK', [('Content-Type', 'application/json'), ('Cache-Control', 'no-store'), ('Content-Length', str(len(body)))])
            return [body]
        method = environment.get('REQUEST_METHOD', 'GET')
        if self.audit and method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            request_id = secrets.token_hex(16)
            self.audit.record('attempt', method, request_id)
            status_code = [500]
            def audited_start(status, headers, exc_info=None):
                status_code[0] = int(status.split(' ', 1)[0])
                return start_response(status, headers, exc_info)
            def response():
                result = None
                try:
                    result = self.application(environment, audited_start)
                    yield from result
                finally:
                    if hasattr(result, 'close'):
                        result.close()
                    self.audit.record('completed', method, request_id, status_code[0])
            return response()
        return self.application(environment, start_response)


def configure_melange_assertions(identity, data_root):
    """User decisions stay in the explicitly selected workspace, never binaries."""
    root = Path(data_root).resolve()
    assertions = (root / 'fec' / 'surfaces' / 'staffer_user_assertions.json').resolve()
    if not assertions.is_relative_to(root):
        raise ValueError('Melange assertions must remain in the selected data directory')
    if assertions.exists() and not assertions.is_file():
        raise ValueError('Melange assertions must be a regular user data file')
    # Do not invent an empty replacement for missing user decisions.
    identity.USER_ASSERTIONS = assertions
    return assertions


def configure_melange_dashboard(module, data_root, state_root):
    """Select an existing local interface without copying workspace assets into binaries."""
    root = Path(data_root).resolve()
    state = Path(state_root).resolve()
    selection = state / 'dashboard-selection.json'
    if not selection.exists():
        return 'Moneyball.html'
    if not selection.resolve().is_relative_to(state) or not selection.is_file() or selection.stat().st_size > 4096:
        raise ValueError('Invalid local Melange dashboard selection file')
    config = json.loads(selection.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict) or set(config) != {'schemaVersion', 'dataRoot', 'dashboardFile'} or type(config['schemaVersion']) is not int or config['schemaVersion'] != 1:
        raise ValueError('Unsupported local Melange dashboard selection')
    selected_root = config['dataRoot']
    if not isinstance(selected_root, str) or not Path(selected_root).is_absolute() or os.path.normcase(str(Path(selected_root).resolve())) != os.path.normcase(str(root)):
        raise ValueError('Melange dashboard selection belongs to a different data directory')
    filename = config['dashboardFile']
    if filename not in ('Moneyball.html', 'Moneyball_Redesign.html'):
        raise ValueError('Melange dashboard must be a supported local basename')
    dashboard = (root / filename).resolve()
    if dashboard.parent != root or not dashboard.is_file():
        raise ValueError('Selected Melange dashboard must exist inside the data directory')
    if filename == 'Moneyball_Redesign.html':
        assets = (root / 'fec' / 'surfaces' / 'staffer_live').resolve()
        approved = (assets / 'approved').resolve()
        if not assets.is_relative_to(root) or not approved.is_relative_to(assets) or not approved.is_dir():
            raise ValueError('Selected Melange interface assets must remain inside the data directory')
        for name in ('mlk-boot.js', 'app.css', 'ui-kit.css', 'forecasts.css', 'ui-kit.js', 'app.js', 'research.js', 'workspace.js'):
            asset = (approved / name).resolve()
            if not asset.is_relative_to(approved) or not asset.is_file():
                raise ValueError('Selected Melange interface is missing a required local asset')
        module.ASSETS = assets
    return filename


def relocate_mentat_modules(code_root, data_root, workspace=None, app_data=None):
    """Relocate module-level data paths, retaining code and static resource locations.

    Unlike hosted redirect_state_writes, this does not disable index construction.
    Source-relative runtime paths are covered in a native full-feature acceptance test.
    """
    read_only_names = {'APP', 'APP_DIR', 'TOOLS', 'TOOLS_DIR', 'STATIC', 'STATIC_DIR', 'ASSETS', 'TEMPLATES', 'HERE', 'SCRIPT', 'CODE_ROOT'}
    for module in list(sys.modules.values()):
        filename = getattr(module, '__file__', None)
        if not filename:
            continue
        try:
            Path(filename).resolve().relative_to(code_root)
        except ValueError:
            continue
        for name, value in list(vars(module).items()):
            if name in read_only_names or not isinstance(value, Path):
                continue
            try:
                relative = value.resolve().relative_to(code_root)
            except (ValueError, OSError):
                continue
            if not relative.parts:
                setattr(module, name, workspace or data_root)
            elif relative.parts[0] in {'state', 'reports', 'inputs', '.cache', 'melange', 'outputs'}:
                setattr(module, name, data_root / relative)
            elif relative.parts[:2] == ('app', 'data'):
                setattr(module, name, (app_data or data_root / 'app-data') / Path(*relative.parts[2:]))


def require_runtime(root, name, relative):
    file = root / 'native' / relative
    if not file.is_file():
        raise RuntimeError(f'{name} runtime missing: {file}. This installer cannot provide full {name} functionality.')
    return file


def run(options, token):
    source = options.source_root.resolve()
    data = options.data_root.resolve()
    state = options.state_root.resolve()
    principal = None
    if options.app == 'terrier':
        sys.path.insert(0, str(source))
        from server.research.desktop_principal import parse_principal
        raw_principal = os.environ.pop('DESKTOP_ACCESS_PRINCIPAL', '')
        if len(raw_principal) > 4096:
            raise ValueError('Approved desktop principal is required')
        principal = parse_principal(json.loads(raw_principal), options.district)
        if (principal.mode == 'existing-owner') != bool(getattr(options, 'legacy_owner', None)):
            raise ValueError('Explicit owner mapping does not match the approved principal')
    for directory in (data, state):
        directory.mkdir(parents=True, exist_ok=True)
    # A caller must not make a desktop install act as a network-hosted server.
    if os.environ.get('ACCESS_ENFORCE') == '1' or os.environ.get('CU_DEPLOYMENT') == 'hosted':
        raise RuntimeError('Desktop startup refuses hosted configuration')
    os.environ['BIND_HOST'] = '127.0.0.1'
    os.environ['DESKTOP_APP_ID'] = options.app
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONNOUSERSITE'] = '1'
    os.environ.pop('PYTHONPATH', None)
    os.environ.pop('PYTHONHOME', None)
    if options.app == 'melange':
        from melange_bootstrap import initialize_workspace
        initialize_workspace(data)
        sys.path.insert(0, str(source))
        os.environ.update(MONEYBALL_ROOT=str(data), MELANGE_LOCAL_ROOT=str(state / 'Melange'),
                          MELANGE_DATA_ROOT=str(data / 'melange-data'), GLASSBOX_EVENTS_DIR=str(state / 'glassbox'))
        office = source.parent / 'native' / 'LibreOffice' / 'program'
        if (office / 'python.exe').is_file() and (office / 'soffice.com').is_file():
            os.environ.update(MELANGE_XL_ENGINE='libreoffice', MELANGE_OFFICE_CALC_LOCAL='1',
                              MELANGE_OFFICE_PYTHON=str(office / 'python.exe'), SOFFICE=str(office / 'soffice.com'))
        configure_melange_assertions(importlib.import_module('fec.surfaces.staffer_identity_resolution'), data)
        from melange_data_paths import configure_melange_workspace
        configure_melange_workspace(data)
        module = importlib.import_module('fec.surfaces.staffer_live_server')
        dashboard_file = configure_melange_dashboard(module, data, state)
        module.Handler = guarded_handler(module.Handler, token, options.app)
        sys.argv = ['staffer_live_server', '--source-root', str(data), '--port', str(options.port), '--dashboard-file', dashboard_file]
        module.main()
    elif options.app == 'mentat':
        code = source / 'mentat'
        from mentat_layout import prepare_workspace, load_connections, bind_analytics_paths
        protected_root = source.parent
        if source.parent.parent.name == 'runtime' and source.parent.parent.parent.name == 'resources':
            protected_root = source.parent.parent.parent.parent
        connections = load_connections(code, data, state, [protected_root])
        workspace = prepare_workspace(code, data, state)
        sys.path[:0] = [str(code / 'app'), str(code / 'tools'), str(source)]
        connections['reports'].mkdir(exist_ok=True)
        os.environ.update(MENTAT_REPORT_DIRECTORY=str(connections['reports']), MENTAT_MELANGE_ROOT=str(data / 'melange'),
                          MENTAT_DATA_ROOT=str(data), MENTAT_BULK_DIR=str(data / 'bulk'),
                          MENTAT_WORKSPACE_ROOT=str(workspace), MENTAT_CODE_ROOT=str(code),
                          MENTAT_INPUTS_DIR=str(data / 'inputs'), MENTAT_CONSOLE_ROOT=str(connections['console']),
                          MENTAT_APP_DATA_ROOT=str(connections['app_data']), MENTAT_FORECAST_DATA_ROOT=str(data / 'forecast'),
                          MENTAT_REPORT_CACHE_ROOT=str(data / '.cache' / 'industry-report'),
                          MENTAT_RECEIPT_ROOTS=json.dumps(connections['receipt_roots']),
                          MENTAT_PREWARM='0', MENTAT_CROSS_REFERENCE_ROOT=str(data / 'state/cross-reference'),
                          MENTAT_ACCEPTED_SOURCE_ROOT=str(data / 'state/accepted-report-sources'),
                          MENTAT_PYTHON=sys.executable, MENTAT_XLSX_WRITER='openpyxl')
        native = source.parent / 'native'
        node = native / 'Node' / 'node.exe'
        if node.is_file():
            os.environ['PATH'] = str(node.parent) + os.pathsep + os.environ.get('PATH', '')
            os.environ.pop('NODE_OPTIONS', None)
            os.environ.pop('NODE_PATH', None)
        from desktop_figure_renderer import install as install_figure_renderer
        install_figure_renderer(importlib.import_module('figure_pdf'))
        module = importlib.import_module('server')
        relocate_mentat_modules(code, data, workspace, connections['app_data'])
        bind_analytics_paths(importlib.import_module('contribution_data'), code, workspace, data)
        module.Handler = guarded_handler(module.Handler, token, options.app)
        rscript = source.parent / 'native' / 'R' / 'bin' / ('Rscript.exe' if os.name == 'nt' else 'Rscript')
        sys.argv = ['mentat', '--port', str(options.port), '--data-root', str(connections['console'])]
        if rscript.is_file():
            os.environ['R_LIBS_USER'] = str(rscript.parent.parent / 'library')
            os.environ['MENTAT_R_LIBRARY'] = os.environ['R_LIBS_USER']
            os.environ['RF_RLIB'] = os.environ['R_LIBS_USER']
            os.environ['R_LIBS_SITE'] = os.environ['R_LIBS_USER']
            for key in ('R_PROFILE', 'R_PROFILE_USER', 'R_ENVIRON', 'R_ENVIRON_USER'):
                os.environ[key] = os.devnull
            sys.argv += ['--rscript', str(rscript)]
        else:
            print('DESKTOP_RUNTIME_WARNING: Rscript is not bundled; collection actions requiring R cannot run.', file=sys.stderr, flush=True)
        module.main()
    else:
        sys.path.insert(0, str(source))
        os.environ.update(CU_LOCAL_ADMIN='0', TERRIER_LOCAL_DIR=str(data), TERRIER_WORK_DIR=str(state / 'work'),
                          TERRIER_SOURCES_DIR=str(data / 'imports'), TERRIER_GEOGRAPHY_DIR=str(data / 'geography'),
                          TERRIER_SIBLING_DIR=str(data / 'baseline'), TERRIER_LUNA_SCRATCH=str(state / 'scratch'),
                          TERRIER_WORK_PY=sys.executable, TERRIER_PY312=sys.executable, TERRIER_COMPUTE='windows')
        legacy_owner = getattr(options, 'legacy_owner', None)
        from terrier_district import validate_district
        validate_district(source, data, options.district, legacy=bool(legacy_owner))
        if legacy_owner:
            from terrier_owner import legacy_secret_path
            key = legacy_secret_path(data, state, legacy_owner)
        else:
            key = state / 'django-secret'
        if not key.exists():
            key.write_text(secrets.token_urlsafe(64), encoding='utf-8')
            if os.name != 'nt':
                key.chmod(0o600)
        os.environ.update(DJANGO_SETTINGS_MODULE='server.settings', CU_DEPLOYMENT='desktop', CU_BIND='127.0.0.1',
                          CU_ALLOWED_HOSTS='127.0.0.1,localhost', CU_DATA_DIR=str(data), CU_STATE_DIR=str(state),
                          CU_SECRET_KEY=key.read_text(encoding='utf-8'), CU_DISTRICT=options.district,
                          CU_COOKIE_PREFIX='desktop_' + options.district.replace('-', '') + '_')
        import django
        django.setup()
        from django.core.management import call_command
        # The explicit selected state store is backed up and verified before any migration.
        from desktop_migrations import migrate_django
        migrate_django(state)
        from server.research.desktop_principal import prepare_principal
        launch_path = prepare_principal(principal, state)
        command = importlib.import_module('server.research.management.commands.serve')
        def desktop_fault_log(port):
            import faulthandler
            log_dir = state / 'logs'
            log_dir.mkdir(exist_ok=True)
            command._fault_log = (log_dir / 'backend-faults.log').open('a', encoding='utf-8')
            faulthandler.enable(file=command._fault_log, all_threads=True)
        command.enable_fault_log = desktop_fault_log
        from desktop_audit import DesktopAudit
        from server.research.desktop_access import DesktopBoundary
        audit = DesktopAudit(state / 'audit', options.app, options.district)
        command.application = GuardedWSGI(command.application, token, options.app, audit, launch_path=launch_path,
                                          desktop_boundary=DesktopBoundary(options.district, options.port, principal))
        call_command('serve', port=options.port)


def main():
    import multiprocessing
    multiprocessing.freeze_support()
    if sys.argv[1:] == ['--desktop-credential']:
        from native_credentials import main as credential_main
        return credential_main()
    if sys.argv[1:] == ['--desktop-access-state']:
        from native_access_state import main as access_state_main
        return access_state_main()
    if sys.argv[1:] == ['--desktop-district-check']:
        from terrier_district import main as district_main
        return district_main()
    if sys.argv[1:] == ['--desktop-owner-check']:
        from terrier_district import main as district_main
        return district_main(owner=True)
    contain_windows_children()
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', choices=['melange', 'mentat', 'terrier'], required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--district', choices=['co-05', 'or-05', 'ct-01'], default='co-05')
    parser.add_argument('--legacy-owner', choices=['admin'])
    options = parser.parse_args()
    token = os.environ.get('DESKTOP_LAUNCH_TOKEN', '')
    if len(token) < 32 or not 1024 <= options.port <= 65535:
        parser.error('A launch token and an unprivileged port are required')
    if options.source_root.resolve() == options.data_root.resolve():
        parser.error('Installed code and writable data must be separate')
    if options.legacy_owner and options.app != 'terrier':
        parser.error('Legacy owner setup is only available for Terrier')
    run(options, token)


if __name__ == '__main__':
    raise SystemExit(main())
