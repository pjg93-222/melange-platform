"""Use the application's isolated Electron capture service for sanitized figures."""
import json
import os
import urllib.parse
import urllib.request


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def install(module):
    def render(document, width, height):
        endpoint = os.environ.get('MENTAT_DESKTOP_RENDER_URL', '')
        token = os.environ.get('MENTAT_DESKTOP_RENDER_TOKEN', '')
        try:
            url = urllib.parse.urlsplit(endpoint)
            valid = (url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port and not url.username and not url.password
                     and url.path == '/render' and not url.query and not url.fragment and len(token) >= 32)
        except ValueError:
            valid = False
        if not valid:
            raise module.BrowserMissing('Desktop image export is unavailable. Restart the application.')
        if not isinstance(document, str) or type(width) is not int or type(height) is not int:
            raise module.BadRequest('Invalid figure dimensions or document.')
        if not (module.MIN_PAGE_PX <= width <= module.MAX_PAGE_PX and module.MIN_PAGE_PX <= height <= module.MAX_PAGE_PX):
            raise module.BadRequest('Invalid figure dimensions.')
        if width * height > 32_000_000:
            raise module.BadRequest('The figure is too large to export.')
        scale = 2 if width * height <= 8_000_000 and width <= 9500 and height <= 9500 else 1
        body = json.dumps({'document': document, 'width': width, 'height': height, 'scale': scale}).encode('utf-8')
        if len(body) > module.MAX_REQUEST_BYTES:
            raise module.BadRequest('The figure is too large to export.')
        request = urllib.request.Request(endpoint, body, headers={'Content-Type':'application/json', 'Authorization':'Bearer '+token}, method='POST')
        try:
            # Ignore host proxy configuration for this one authenticated loopback call.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects())
            with opener.open(request, timeout=module.RENDER_TIMEOUT_SECONDS) as response:
                if response.status != 200 or response.headers.get_content_type() != 'image/png':
                    raise ValueError('Unexpected render response')
                png = response.read(100 * 1024 * 1024 + 1)
            if len(png) > 100 * 1024 * 1024 or not png.startswith(module.PNG_SIGNATURE) or not png.endswith(module.PNG_END):
                raise ValueError('Incomplete image')
            return png
        except TimeoutError:
            raise module.RenderTimeout('The image took longer than expected to export.') from None
        except (OSError, ValueError):
            raise module.RenderFailed('The desktop image export could not finish. Try again.') from None
    module.render = render
