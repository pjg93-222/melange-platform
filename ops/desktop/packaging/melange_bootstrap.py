"""Initialize an empty desktop workspace without bundling the publisher's data."""
from pathlib import Path


WELCOME = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Melange — Your workspace</title>
<style>
body{margin:0;background:#101820;color:#eef2f5;font:17px/1.6 system-ui,sans-serif}
main{max-width:720px;margin:12vh auto;padding:32px}h1{font-size:42px;line-height:1.15}
p{color:#cbd5df}a{color:#9bd8f5}li{margin:12px 0}code{font-size:15px}
.note{border-left:3px solid #91b8ca;padding-left:20px;margin-top:32px}
</style></head><body><main>
<!-- melange-desktop-empty-workspace-v1 -->
<h1>Your Melange workspace</h1>
<p>This installation starts with your own local data. No administrator records,
accounts, or credentials are included.</p>
<h2>Open an existing dashboard</h2>
<ol><li>Close the app after saving any work.</li>
<li>Open Melange again and choose <strong>Settings</strong> from the Melange menu.</li>
<li>Choose your approved Melange data folder containing <code>Moneyball.html</code>,
then open the app. Use a folder you are authorized to access.</li></ol>
<p>Your existing dashboard keeps its network, member, industry, and staffer views.</p>
<p><a href="/staffer-live/">Open the staffer workspace</a></p>
<p class="note">This folder is currently empty. Data-dependent views and calculations
need their corresponding source data. Updates change the program and leave your
local data separate.</p>
</main></body></html>
"""


def initialize_workspace(data_root: Path) -> bool:
    """Create the first-run page once; never replace a user's dashboard or data."""
    root = Path(data_root)
    if not root.is_dir():
        raise ValueError("Choose an existing Melange data directory")
    target = root / "Moneyball.html"
    if target.is_symlink():
        raise ValueError("The desktop dashboard must be a regular file in its data directory")
    if target.exists() and not target.is_file():
        raise ValueError("Moneyball.html is not a regular file")
    try:
        # Exclusive creation protects an existing artifact and simultaneous launches.
        with target.open("x", encoding="utf-8", newline="\n") as page:
            page.write(WELCOME)
    except FileExistsError:
        if not target.is_file():
            raise ValueError("Moneyball.html is not a regular file")
        return False
    return True
