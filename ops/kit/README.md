# Melange install kit

Two audiences, two files. Check the download against `SHA256SUMS` first.

## Invited users (Windows)

Run once, with the domain and the apps you were invited to:

```powershell
.\Install-MelangeApps.ps1 -Domain example.org -Apps melange,co05
```

This only creates Start Menu shortcuts that open `https://<app>.<domain>/` in an Edge app window.
No code, token or password is installed. You sign in through the Cloudflare Access page with the
email address you were invited on. Apps: `melange`, `mentat`, `co05`, `or05`, `ct01`. Remove the
shortcuts with `-Remove`.

## Stand up another server (admin)

For an existing Ubuntu 24.04 host with Docker; a brand-new host should use `vps/cloud-init.yaml`,
which does this and the hardening too.

```bash
sudo ./install.sh --manifest manifest.json     # manifest.json.sig beside it
```

It refuses to continue unless the manifest signature verifies against
`/etc/melange/allowed_signers`, writes `compose.env` from the signed digests (your DOMAIN, TEAM and
AUD values are kept), pulls exactly those digests, and starts the apps once data has been pushed.
Contents: `install.sh`, `compose.yaml`, `compose.env.example`, `images.lock`, `digests.json`,
`vps/` (updater, host scripts, systemd units, `host-files.toml`, cloud-init).

Everything after the first boot (tunnel token, registry login, Access values) is in `ops/VPS.md`.
