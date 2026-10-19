"""Policy tests for ops/compose.yaml, compose.env.example and the VPS units/scripts.

Skipped with a message when PyYAML is missing. `docker compose config` is not needed (the
security gate's images stage runs that in CI)."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
yaml = pytest.importorskip("yaml", reason="PyYAML not installed; compose policy not checked")

sys.path.insert(0, str(OPS.parent))
from ops.tests.layout import src  # noqa: E402

WF_DIR = src("ops/github/workflows")     # the source-only export (CI) remaps ops/github/ to .github/

DOC = yaml.safe_load((OPS / "compose.yaml").read_text(encoding="utf-8"))
SERVICES = DOC["services"]
APPS = {n: s for n, s in SERVICES.items() if n != "cloudflared"}
MEM = {"melange-desk": "512m", "mentat-console": "6g", "terrier-co05": "2g", "terrier-or05": "2g",
       "terrier-ct01": "2g", "cloudflared": "256m"}


def test_expected_services():
    assert set(SERVICES) == set(MEM)


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_no_published_ports_or_privileges(name):
    s = SERVICES[name]
    assert "ports" not in s
    for key in ("privileged", "network_mode", "cap_add", "devices", "pid", "ipc", "userns_mode"):
        assert key not in s, key


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_hardening_anchor_applied(name):
    s = SERVICES[name]
    assert s["read_only"] is True
    assert s["user"] == "10001:10001"
    assert s["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in s["security_opt"]
    assert any(t.startswith("/tmp") for t in s["tmpfs"])
    assert s["pids_limit"] > 0 and s["restart"] == "unless-stopped"
    assert s["logging"]["driver"] == "journald"
    assert s["mem_limit"] == MEM[name]
    assert s["image"].startswith("${IMAGE_")


APP_NETS = {"melange-desk": "desk", "mentat-console": "mentat", "terrier-co05": "co05", "terrier-or05": "or05", "terrier-ct01": "ct01"}


def test_every_app_has_its_own_internal_network_and_only_cloudflared_joins_them_all_plus_egress():
    nets = DOC["networks"]
    assert set(nets) == set(APP_NETS.values()) | {"egress"} and "internal" not in nets["egress"]
    for name, net in APP_NETS.items():
        assert nets[net]["internal"] is True and SERVICES[name]["networks"] == [net], name
    assert SERVICES["cloudflared"]["networks"] == [*APP_NETS.values(), "egress"]
    # districts (and apps) cannot reach each other at the network level: no two apps share a network
    assert not [(a, b) for a in APP_NETS for b in APP_NETS if a < b and set(SERVICES[a]["networks"]) & set(SERVICES[b]["networks"])]


def test_data_mounts_are_read_only_and_never_auto_created():
    for name, s in APPS.items():
        for v in s["volumes"]:
            if isinstance(v, dict) and v["type"] == "bind":
                assert v["read_only"] is True, (name, v["source"])
                assert v["bind"]["create_host_path"] is False, (name, v["source"])
                assert v["source"].startswith(("/srv/data/", "/srv/access")), (name, v["source"])
            else:  # named volume: "<name>:<target>" with no :ro, state only
                assert re.match(r"^(mentat-data|mentat-cache|terrier-(co05|or05|ct01)-state):/", v), (name, v)
    for name, s in APPS.items():
        targets = {v["target"]: v for v in s["volumes"] if isinstance(v, dict)}
        assert targets["/run/access"]["source"] == "/srv/access"
        assert targets["/run/snapshot_id"]["read_only"] is True
        if name.startswith("terrier"):
            assert targets["/data"]["source"].startswith("/srv/data/terrier/current/")  # this district only


DISTRICTS = {"co05": "co-05", "or05": "or-05", "ct01": "ct-01"}      # service/host name -> district id = serve-set dest dir


def test_terrier_districts_cannot_see_each_other():
    for d, district in DISTRICTS.items():
        s = SERVICES[f"terrier-{d}"]
        binds = [v["source"] for v in s["volumes"] if isinstance(v, dict) and v["target"] == "/data"]
        assert binds == [f"/srv/data/terrier/current/{district}"]
        assert s["environment"]["CU_DISTRICT"] == district
        assert s["env_file"] == f"/srv/serve/secrets/terrier-{d}.env"
        assert "CU_SECRET_KEY" not in s["environment"]          # only the root-only env_file carries it


def test_terrier_data_is_read_only_at_CU_DATA_DIR_and_state_is_the_external_volume_at_CU_STATE_DIR():
    for d in DISTRICTS:
        s = SERVICES[f"terrier-{d}"]
        env = s["environment"]
        assert env["CU_DATA_DIR"] == "/data" and env["CU_STATE_DIR"] == "/state" and env["CU_DEPLOYMENT"] == "hosted"
        assert env["ACCESS_ENFORCE"] == "1" and env["CU_BIND"] == "0.0.0.0"
        assert [v for v in s["volumes"] if isinstance(v, str)] == [f"terrier-{d}-state:/state"]     # writable: the state volume only
        assert env["CU_BUNDLE"] == env["CU_DATA_DIR"] + "/bundle.zip"
        urls = dict(item.split("=", 1) for item in env["CU_DISTRICT_URLS"].split(","))              # comma list of district=https://host
        assert set(urls) == set(DISTRICTS.values())
        assert all(re.fullmatch(r"https://(co05|or05|ct01)\.\$\{DOMAIN[^}]*\}", u) for u in urls.values()), urls


def test_serve_sets_and_compose_agree_on_district_dirs_bundle_names_and_the_desk_set():
    sets = json.loads((OPS / "serve_sets.json").read_text(encoding="utf-8"))["systems"]
    rules = sets["terrier"]["rules"]
    assert {r["dest"] for r in rules} == set(DISTRICTS.values())
    for r in rules:
        if r.get("kind") == "newest_pair":
            assert r["as"] == "bundle.zip" and r["companion"] == ".sha256", r        # CU_BUNDLE=/data/bundle.zip, bundle.zip.sha256 beside it
    mounted = {v["source"] for d in DISTRICTS for v in SERVICES[f"terrier-{d}"]["volumes"] if isinstance(v, dict) and v["target"] == "/data"}
    assert mounted == {f"/srv/data/terrier/current/{dest}" for dest in DISTRICTS.values()}
    assert sets["terrier"]["data_subdir"] == "terrier"
    # the desk serves DESK_ROOT=/data/melange = the melange tree: pages, their .gz siblings (desk_server prefers them), the build manifest
    desk = SERVICES["melange-desk"]
    root = desk["environment"]["DESK_ROOT"]
    assert [v["source"] for v in desk["volumes"] if isinstance(v, dict) and v["target"] == root] == ["/srv/data/melange/current"]
    (rule,) = sets["melange"]["rules"]
    assert rule["dest"] == "" and sets["melange"]["data_subdir"] == "melange"
    assert set(rule["include"]) == {"Moneyball.html", "Moneyball.html.gz", "lobby-*.html", "lobby-*.html.gz", "INVITED_BUILD.json"}


def test_mentat_mounts_cover_every_directory_of_the_mentat_serve_set_where_the_console_looks():
    sets = json.loads((OPS / "serve_sets.json").read_text(encoding="utf-8"))["systems"]["mentat"]
    s = SERVICES["mentat-console"]
    prefix = "/srv/data/mentat/current/"
    mounts = {v["source"][len(prefix):]: v["target"] for v in s["volumes"]
              if isinstance(v, dict) and v["source"].startswith(prefix) and not v["source"].endswith(".snapshot_id")}
    for rule in sets["rules"]:
        dest = rule.get("dest", "").strip("/")
        assert any(dest == m or dest.startswith(m + "/") for m in mounts), f"{dest} is not under a read-only mount"
    env = s["environment"]
    # PROJECT is /app/mentat (server.py): state, .cache, inputs, forecast and app/data are looked up there; reports and melange by env
    assert {t for m, t in mounts.items() if m not in ("reports", "melange")} == {f"/app/mentat/{m}" for m in ("state", ".cache", "inputs", "forecast", "app/data")}
    assert mounts["reports"] == env["MENTAT_REPORT_DIRECTORY"] and mounts["melange"] == env["MENTAT_MELANGE_ROOT"]
    assert env["MENTAT_DONOR_ROW_EMAILS"] == ""                                       # present and empty: nobody sees donor rows yet
    assert env["MENTAT_STATE_WRITE_ROOT"] == "/var/cache/mentat" and "mentat-cache:/var/cache/mentat" in s["volumes"]
    image = (OPS / "docker" / "mentat.Dockerfile").read_text(encoding="utf-8")
    assert f"PYTHONPATH={env['PYTHONPATH']}" in image and env["PYTHONPATH"] == "/app/mentat/app:/app/mentat/tools"
    assert '"--data-root", "/var/lib/mentat"' in image and "mentat-data:/var/lib/mentat" in s["volumes"]


def test_desk_image_runs_the_module_whose_main_returns_the_exit_code():
    text = (OPS / "docker" / "melange-desk.Dockerfile").read_text(encoding="utf-8")
    cmd = json.loads(re.search(r"^CMD (\[.*\])$", text, re.M).group(1))
    assert cmd == ["python", "-m", "fec.surfaces.desk_server"]
    source = (OPS.parent / "fec" / "surfaces" / "desk_server.py").read_text(encoding="utf-8")
    assert re.search(r"^def main\(\):", source, re.M) and 'if __name__ == "__main__":\n    sys.exit(main())' in source
    assert "COPY fec/access_gate.py" in text and "COPY fec/surfaces/desk_server.py" in text


def test_terrier_state_volumes_are_external_so_down_v_cannot_remove_them():
    for d in ("co05", "or05", "ct01"):
        assert DOC["volumes"][f"terrier-{d}-state"] == {"external": True}


def test_apps_have_loopback_healthchecks():
    for name, s in APPS.items():
        probe = s["healthcheck"]["test"][-1]
        assert "127.0.0.1" in probe and ("/api/health" if name.startswith("terrier") else "/healthz") in probe


def test_apps_get_the_access_env_contract():
    for name, s in APPS.items():
        env = s["environment"]
        for key in ("ACCESS_AUD", "ACCESS_ISSUER", "ACCESS_JWKS_FILE", "ACCESS_SERVICE_CNS", "SNAPSHOT_ID_FILE"):
            assert key in env, (name, key)
        assert env["ACCESS_JWKS_FILE"] == "/run/access/jwks.json"
        assert ("CU_BIND" in env) == name.startswith("terrier") and ("BIND_HOST" in env) == (not name.startswith("terrier"))
        assert env["ACCESS_ENFORCE"] == "1", name
    t = SERVICES["terrier-or05"]["environment"]
    assert t["CU_DEPLOYMENT"] == "hosted" and t["CU_DISTRICT"] == "or-05" and t["CU_BUNDLE"] == "/data/bundle.zip"
    m = SERVICES["mentat-console"]["environment"]
    assert m["MENTAT_STATE_WRITE_ROOT"] == "/var/cache/mentat" and m["MENTAT_REPORT_DIRECTORY"] == "/data/mentat/reports"


def test_every_compose_variable_is_defined_or_managed():
    text = (OPS / "compose.yaml").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\{([A-Z0-9_]+)", text))
    example = (OPS / "compose.env.example").read_text(encoding="utf-8")
    defined = {ln.split("=", 1)[0] for ln in example.splitlines() if "=" in ln and not ln.startswith("#")}
    managed = {"RELEASE_ID"} | {k for k in used if k.startswith("IMAGE_") and k != "IMAGE_CLOUDFLARED"}
    assert not (used - defined - managed), used - defined - managed


def test_updater_manages_exactly_the_image_variables_compose_expects():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("melange_update", OPS / "vps" / "melange_update.py")
    mu = importlib.util.module_from_spec(spec)
    sys.modules["melange_update"] = mu
    spec.loader.exec_module(mu)
    text = (OPS / "compose.yaml").read_text(encoding="utf-8")
    wanted = {k for k in re.findall(r"\$\{(IMAGE_[A-Z0-9_]+)", text) if k != "IMAGE_CLOUDFLARED"}
    assert wanted == {mu.image_var(n) for s in mu.SYSTEMS.values() for n in s}
    assert {n for s in mu.SYSTEMS.values() for n in s} == set(APPS)


@pytest.mark.parametrize("script", ["vps/melange-ops.sh", "vps/melange-bootstrap.sh", "kit/install.sh"])
def test_shell_scripts_parse(script):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    p = OPS / script
    r = subprocess.run([bash, "-n", p.name], cwd=p.parent, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_every_unit_has_its_pair_and_exec_target():
    units = {p.name: p.read_text(encoding="utf-8") for p in (OPS / "vps" / "systemd").iterdir()}
    for t in [n for n in units if n.endswith(".timer")]:
        assert t[:-6] + ".service" in units, t
    assert "melange-update.path" in units
    for name, body in units.items():
        for m in re.finditer(r"^ExecStart=(\S+)(?: (\S+))?", body, re.M):
            exe = m.group(1)
            assert exe.startswith("/"), (name, exe)
            if exe.endswith("melange-ops.sh"):
                assert m.group(2) in {"jwks", "backup", "ping"}, name
    assert "Persistent=true" in units["melange-update.timer"] and "hourly" in units["melange-update.timer"]


# --- cloud-init, secrets, docs, workflows -------------------------------------------------

import importlib.util
import json
import sys


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, OPS / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


BUILDER = _load("build_cloud_init", "vps/build_cloud_init.py")
CLOUD = (OPS / "vps" / "cloud-init.yaml").read_text(encoding="utf-8").replace("\r\n", "\n")
BOOT = (OPS / "vps" / "melange-bootstrap.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
CLOUD_DOC = yaml.safe_load(CLOUD)
CLOUD_FILES = {f["path"]: f["content"] for f in CLOUD_DOC["write_files"] if f.get("encoding") != "b64"}
# the static host configuration (sshd, Docker daemon, audit...) travels in the payload: host-files.toml
CLOUD_FILES.update({"/" + n: d.decode() for n, (d, _) in BUILDER.embedded(CLOUD).items()})


def test_cloud_init_payload_is_current():
    assert BUILDER.embedded(CLOUD) == BUILDER.members(), "stale: run python ops/vps/build_cloud_init.py"


def test_cloud_init_payload_unpacks_to_the_right_places_and_modes():
    got = BUILDER.embedded(CLOUD)
    assert got["usr/local/lib/melange/melange_update.py"][1] == 0o755
    assert got["usr/local/sbin/melange-bootstrap"][1] == 0o750
    assert got["srv/serve/compose.yaml"][0].startswith(b"# Serve stack")
    assert {n for n in got if n.startswith("etc/systemd/system/") and n.count("/") == 3} == \
        {f"etc/systemd/system/{p.name}" for p in (OPS / "vps" / "systemd").iterdir()}      # drop-ins live in host-files.toml
    assert got["etc/audit/rules.d/50-melange.rules"][1] == 0o640 and got["etc/ssh/sshd_config.d/10-melange.conf"][1] == 0o644


def test_cloud_init_fits_a_32k_user_data_limit():
    assert len(CLOUD.encode()) < 32 * 1024, "too big for 32 KiB providers; trim comments or move content into the payload"


def test_cloud_init_hardening_contract():
    assert CLOUD.startswith("#cloud-config")
    assert set(CLOUD_DOC["packages"]) >= {"restic", "age", "sqlite3", "auditd", "unattended-upgrades",
                                          "cryptsetup", "jq", "curl", "ufw"}
    users = {u["name"]: u for u in CLOUD_DOC["users"]}
    assert set(users) == {"admin", "melange-push"}
    assert "sudo" in users["admin"]["groups"] and users["melange-push"]["shell"] == "/usr/sbin/nologin"
    daemon = json.loads(CLOUD_FILES["/etc/docker/daemon.json"])
    assert daemon["userns-remap"] == "default" and daemon["no-new-privileges"] is True
    assert daemon["icc"] is False and daemon["live-restore"] is True and daemon["log-driver"] == "journald"
    assert daemon["data-root"].startswith("/srv/data/")  # encrypted volume, not the boot disk
    sshd = CLOUD_FILES["/etc/ssh/sshd_config.d/10-melange.conf"]
    for line in ("ListenAddress 127.0.0.1", "PasswordAuthentication no", "KbdInteractiveAuthentication no",
                 "PermitRootLogin no", "Match User melange-push", "ForceCommand internal-sftp",
                 "ChrootDirectory /srv/melange/push"):
        assert line in sshd, line
    socket = CLOUD_FILES["/etc/systemd/system/ssh.socket.d/10-melange.conf"]
    assert "ListenStream=127.0.0.1:22" in socket and "ListenStream=0.0.0.0" not in socket
    assert socket.count("ListenStream=\n") == 1  # reset first, so the distro's wildcard listener is gone
    journald = CLOUD_FILES["/etc/systemd/journald.conf.d/50-melange.conf"]
    assert "MaxRetentionSec=90day" in journald and "SystemMaxUse=" in journald
    rules = CLOUD_FILES["/etc/audit/rules.d/50-melange.rules"]
    for key in ("sshd_config", "sudoers", "docker", "cryptsetup"):
        assert key in rules, key
    uu = CLOUD_FILES["/etc/apt/apt.conf.d/52melange-unattended-upgrades"]
    assert "-security" in uu and 'Automatic-Reboot "false"' in uu and "-updates" not in uu
    assert "ufw default deny incoming" in BOOT and "ufw --force enable" in BOOT
    assert "Docker apt key fingerprint" in BOOT and "apt-mark hold" in BOOT


def test_luks_never_formats_a_device_that_holds_something():
    order = [BOOT.index(m) for m in ("cryptsetup isLuks", "wipefs --no-act", "lsblk -nro MOUNTPOINT",
                                     "cryptsetup luksFormat")]
    assert order == sorted(order), "every guard rail must come before luksFormat"
    assert "--type luks2" in BOOT and "head -c 4096 /dev/urandom" in BOOT and "chmod 0400" in BOOT
    assert "/etc/cryptsetup-keys.d/melange-data.key" in BOOT
    assert "/etc/crypttab" in BOOT and "/etc/fstab" in BOOT
    assert "unset_var DATA_DEVICE" in BOOT  # the variable Paul sets; nothing is formatted without it
    docker_fn = BOOT[BOOT.index("docker_stage() {"):BOOT.index("payload_stage() {")]
    assert "mountpoint -q /srv/data" in docker_fn  # no volume, no Docker


SHIPPED = [CLOUD[:CLOUD.index("  # BEGIN PAYLOAD")], BOOT,
           (OPS / "compose.env.example").read_text(encoding="utf-8"),
           (OPS / "kit" / "install.sh").read_text(encoding="utf-8"),
           (OPS / "vps" / "melange-ops.sh").read_text(encoding="utf-8"),
           (OPS / "compose.yaml").read_text(encoding="utf-8"), (OPS / "vps" / "host-files.toml").read_text(encoding="utf-8")]


@pytest.mark.parametrize("pattern", [
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"ghp_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}",
    r"AGE-SECRET-KEY-", r"eyJ[A-Za-z0-9_-]{20,}",
    r"(?i)(secret|token|password|passwd)\s*[:=]\s*[\"']?[A-Za-z0-9+/_-]{32,}"])
def test_no_secrets_in_shipped_text(pattern):
    for text in SHIPPED:
        assert not re.search(pattern, text), pattern


def test_every_paste_marker_has_a_checklist_entry():
    docs = (OPS / "VPS.md").read_text(encoding="utf-8")
    token = re.compile(r"PASTE-[A-Z0-9]+(?:-[A-Z0-9]+)*")
    markers = {m for text in SHIPPED for m in token.findall(text)}
    assert {"PASTE-TUNNEL-TOKEN", "PASTE-ALLOWED-SIGNERS-LINE", "PASTE-AGE-RECIPIENT", "PASTE-ADMIN-PUBKEY",
            "PASTE-DATA-DEVICE", "PASTE-YOUR-DOMAIN", "PASTE-TEAM-NAME"} <= markers
    missing = sorted(m for m in markers if m not in docs)
    assert not missing, f"VPS.md has no checklist row for {missing}"


def test_release_workflow_runs_the_no_data_test_list():
    def listed(name):
        return [ln for ln in (OPS / "ci" / name).read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    assert listed("no_data_tests.txt") == ["ops/tests/*.py"]
    assert listed("mentat_tests.txt") == ["mentat/tests/test_hosted_mode.py", "mentat/tests/test_contribution_data.py"]
    workflow = (WF_DIR / "release.yml").read_text(encoding="utf-8")
    assert "ops/ci/no_data_tests.txt" in workflow and "ops/ci/mentat_tests.txt" in workflow
    # the access-gate and hosted-mode tests skip without cryptography: the job installs the pinned version explicitly
    assert re.search(r"pip install .*\bcryptography==49\.0\.0\b", workflow)
    assert "cryptography==49.0.0" in (OPS.parent / "requirements.txt").read_text(encoding="utf-8")


WORKFLOWS = {rel: yaml.safe_load((WF_DIR / rel.rsplit("/", 1)[1]).read_text(encoding="utf-8"))
             for rel in ("github/workflows/release.yml", "github/workflows/deps.yml")}


def test_every_action_is_pinned_to_a_full_commit_sha_with_its_tag_as_a_comment():
    for path in WF_DIR.glob("*.yml"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*(-\s+)?uses:", line):
                assert re.search(r"uses:\s*[\w./-]+@[0-9a-f]{40}\s+# v\d+\s*$", line), f"{path.name}:{n}: {line.strip()}"


def test_release_and_updater_accept_exactly_the_same_restic_paths():
    sys.path.insert(0, str(OPS.parent))
    from ops.release_lib import data
    updater = _load("melange_update_paths", "vps/melange_update.py")
    assert data.TREE_PATH.pattern == updater.RESTIC_PATH.pattern
    farm = r"C:\Users\pgoff\AppData\Local\Melange\releases\staging\2026.10.12.1\farm"
    assert updater.RESTIC_PATH.match(data.restic_tree_path(farm)) and data.restic_tree_path(farm).startswith("/C/Users/")


def test_every_ops_file_ci_and_the_images_read_survives_the_source_only_export():
    """CI runs from the platform repo, which export.py fills from an extension allowlist (.txt, .example and .json lists
    are dropped unless release_config.json names them). The gate reads its canary list from ops/security/."""
    sys.path.insert(0, str(OPS.parent))
    from ops.release_lib import export
    cfg = json.loads((OPS / "release_config.json").read_text(encoding="utf-8"))["export"]
    text = "\n".join([(WF_DIR / n).read_text(encoding="utf-8") for n in ("release.yml", "deps.yml")]
                     + [(OPS / rel).read_text(encoding="utf-8") for rel in ("docker/melange-desk.Dockerfile", "docker/mentat.Dockerfile")])
    needed = {p for p in re.findall(r"\bops/[A-Za-z0-9_./-]+", text) if (OPS.parent / p).is_file()}
    needed |= {"ops/security/canaries.txt", "ops/security/exceptions.toml",
               "ops/required_inputs.txt"}   # read by ops/tests/test_chain_parity.py, which the platform CI runs
    listed = ["ops/desktop/client/" + n for n in re.findall(r"'([^']+)'", (OPS / "desktop/client/release-files.cjs").read_text(encoding="utf-8"))]
    sys.path.insert(0, str(OPS))
    import security_gate
    # tests/release-files.test.cjs reads every file the desktop builder lists, except the extensions the security gate denies;
    # a file the gate pins by path and hash (the loading .mp4, owner ruling 2026-10-19) ships and so must survive the export
    needed |= {p for p in listed if (OPS.parent / p).is_file()
               and (Path(p).suffix.lower() not in security_gate.DENY_EXT or p in security_gate.PINNED_FILES)}
    assert {"ops/ci/no_data_tests.txt", "ops/ci/mentat_tests.txt", "ops/locks/mentat-console.linux.txt", "ops/locks/r-packages.txt",
            "ops/compose.env.example",
            "ops/images.lock", "ops/compose.yaml", "ops/security_gate.py"} <= needed
    dropped = sorted(p for p in needed if not export.allowed_name(export.remap(p), cfg))
    assert not dropped, f"the export would drop {dropped}: add the names to release_config.json export.extra_names"


def test_desk_server_answers_for_the_downloads_tree_the_release_builds(tmp_path):
    """build_downloads -> the snapshot's melange/ tree -> DESK_ROOT: /downloads/<id>/ and /downloads/latest/ both answer."""
    import base64
    import shutil
    import threading
    import urllib.request
    sys.path.insert(0, str(OPS.parent))
    sys.path.insert(0, str(OPS.parent / "fec"))
    import access_gate
    from surfaces import desk_server
    from ops.release_lib import manifest
    import hashlib
    b64 = lambda n: base64.b64encode(bytes([1]) * n).decode()
    ciphertext = base64.b64encode(b"".join(hashlib.sha256(bytes([i])).digest() for i in range(129))[:4096]).decode()   # incompressible
    page = f'<html><script>var D={{s:"{b64(16)}",i:"{b64(12)}",c:"{ciphertext}",n:250000}};/* AES-GCM */</script></html>'.encode()
    desk, rid = tmp_path / "desk.html", "2026.10.12.7"
    desk.write_bytes(page)
    farm = tmp_path / "farm"
    for src, dest in manifest.build_downloads({"downloads": {"workbooks": []}}, rid, tmp_path / "pkg", desk, None)["extra"]:
        (farm / dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, farm / dest)                                  # what the restored snapshot holds
    (farm / "melange" / "Moneyball.html").write_bytes(b"<html>invited</html>")
    server = desk_server.make_server(farm / "melange", access_gate.AccessConfig.from_env({}), "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    try:
        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}{path}", timeout=10) as r:
                return r.status, r.read()
        assert get("/")[0] == 200
        assert get(f"/downloads/{rid}/melange-desk-{rid}.html") == (200, page)
        assert get("/downloads/latest/melange-desk.html") == (200, page)
        assert get("/downloads/latest/SHA256SUMS")[1].decode().endswith("  melange-desk.html\n")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


def test_the_workflow_calls_the_security_gate_with_its_real_cli():
    help_text = subprocess.run([sys.executable, str(OPS / "security_gate.py"), "--help"], capture_output=True, text=True).stdout
    stages = set(re.search(r"\{([a-z,]+)\}", help_text).group(1).split(","))
    calls = []
    for wf in WORKFLOWS.values():
        for job in wf["jobs"].values():
            for step in job["steps"]:
                joined = re.sub(r"\\\n\s*", " ", step.get("run", ""))              # fold shell line continuations
                calls += [m.group(1).split() for m in re.finditer(r"ops/security_gate\.py ([^\n]*)", joined)]
    assert {c[0] for c in calls} == {"export", "images"} and {c[0] for c in calls} <= stages
    for argv in calls:
        flags = [a for a in argv if a.startswith("--")]
        assert flags and all(f in help_text for f in flags), argv
    images = next(c for c in calls if c[0] == "images")
    assert images.count("--image") == 1 and "--compose-json" in images and "--ci" in images       # --image takes several refs
    export = next(c for c in calls if c[0] == "export")
    assert "--target" in export and "--ci" in export


@pytest.mark.parametrize("rel", sorted(WORKFLOWS))
def test_workflows_are_least_privilege(rel):
    wf = WORKFLOWS[rel]
    assert wf["permissions"] == {}
    for name, job in wf["jobs"].items():
        perms = job.get("permissions")
        assert perms, (rel, name)
        if perms.get("packages") == "write":
            assert name == "build", (rel, name)
        if perms.get("contents") == "write":
            assert name in {"kit", "publish", "release"}, (rel, name)
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), f"{rel}:{name}: expression inside run (pass it via env)"


def test_release_workflow_has_the_required_stages():
    jobs = WORKFLOWS["github/workflows/release.yml"]["jobs"]
    assert {"test", "security", "build", "sbom", "scan", "kit"} <= set(jobs)
    assert jobs["build"]["needs"] == ["test", "security"]
    push = next(s for s in jobs["build"]["steps"] if s.get("id") == "push")["with"]
    assert push["provenance"] is False and push["no-cache"] is True and not {"cache-from", "cache-to"} & set(push)
    assert {m["name"] for m in jobs["build"]["strategy"]["matrix"]["include"]} == {"melange-desk", "mentat-console"}
    last = jobs["kit"]["steps"][-1]["run"]
    assert "gh release create" in last and "--draft" in last


def test_deps_workflow_is_linux_only_needs_no_geo_token_and_builds_the_r_bundle_from_exact_pins():
    """The three failures of the first deps run: a win_amd64 wheelhouse (hashes are Linux-only), a cross-repo token that does not
    exist, and an R snapshot date nobody had ratified (exact per-package pins, decided 2026-10-12, replaced the date)."""
    raw = (WF_DIR / "deps.yml").read_text(encoding="utf-8")
    jobs = WORKFLOWS["github/workflows/deps.yml"]["jobs"]
    assert "win_amd64" not in raw and "GEO_READ_TOKEN" not in raw and "pjg93-222/Geo" not in raw
    assert set(jobs) == {"key", "wheelhouse", "r-bundle", "publish"}
    assert jobs["r-bundle"]["if"] == "needs.key.outputs.exists != 'true'"                # on again, same test as the wheelhouse
    assert "exact per-package pins (decided 2026-10-12)" in raw and "TODO(Paul)" not in raw
    assert "ops/locks/r-package-repos.txt" in raw
    assert jobs["publish"]["needs"] == ["key", "wheelhouse", "r-bundle"]                 # both skipped together when the release exists


def test_images_lock_has_real_digests_and_every_reference_to_them_matches():
    lock = json.loads((OPS / "images.lock").read_text(encoding="utf-8"))
    for name in ("python", "node", "cloudflared"):
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", lock[name]["digest"]) and set(lock[name]["digest"][7:]) != {"0"}, name
        assert lock[name]["resolved_version"][0].isdigit(), name
    # D-181 (2026-10-12): Debian 13 bases. A bookworm ref anywhere is a regression, not a refresh.
    assert lock["python"]["ref"] == "python:3.12-slim-trixie" and lock["python"]["resolved_version"].startswith("3.12.")
    assert lock["node"]["ref"] == "node:22-trixie-slim" and lock["node"]["resolved_version"].startswith("22.")
    for rel in ("docker/melange-desk.Dockerfile", "docker/mentat.Dockerfile"):
        text = (OPS / rel).read_text(encoding="utf-8")
        assert f"ARG PYTHON_BASE={lock['python']['ref']}@{lock['python']['digest']}\n" in text, rel     # never floats, even without --build-arg
        assert "USER 10001:10001" in text and "HEALTHCHECK" in text and "PYTHONDONTWRITEBYTECODE=1" in text
        assert "ARG GIT_SHA=unknown" in text and "ARG BUILD_TIME=unknown" in text and "GIT_SHA=${GIT_SHA}" in text
        assert "bookworm" not in text.lower(), rel
    example = (OPS / "compose.env.example").read_text(encoding="utf-8")
    assert f"IMAGE_CLOUDFLARED=cloudflare/cloudflared@{lock['cloudflared']['digest']}\n" in example
    assert lock["cloudflared"]["ref"] == f"cloudflare/cloudflared:{lock['cloudflared']['resolved_version']}"
