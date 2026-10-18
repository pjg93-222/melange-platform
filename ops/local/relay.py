"""Loopback relay for the local serve profile (ops/compose.local.yaml).

Without Cloudflare Access the apps listen on 127.0.0.1 only (access_gate refuses an unenforced non-loopback bind;
Terrier's serve refuses CU_BIND beyond loopback unless hosted), and a published container port never reaches
127.0.0.1 inside a container. Each app therefore joins this relay's network namespace: the relay listens on the
container's own address at PORT and copies bytes, unparsed and unchanged, to 127.0.0.1:PORT where the app listens.
The app's own Host, Origin and token checks see exactly what the browser sent. Every peer reaches the app as
127.0.0.1, so compose publishes PORT on the host's 127.0.0.1 only. Stdlib only; it runs in the app's own image.

    python relay.py PORT
"""
import socket
import sys
import threading


def _pipe(src, dst):
    try:
        while data := src.recv(65536):
            dst.sendall(data)
    except OSError:
        pass
    try:
        dst.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def _handle(client, port):
    with client:
        try:
            upstream = socket.create_connection(("127.0.0.1", port), timeout=10)
        except OSError:
            return
        with upstream:
            upstream.settimeout(None)
            back = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
            back.start()
            _pipe(client, upstream)
            back.join()


def serve(listener, port):
    # ponytail: two threads per open connection, capped by the container's pids_limit; enough for one local user.
    while True:
        client, _ = listener.accept()
        try:
            threading.Thread(target=_handle, args=(client, port), daemon=True).start()
        except RuntimeError:   # pids_limit reached: refuse this one connection, keep serving
            client.close()


if __name__ == "__main__":
    port = int(sys.argv[1])
    serve(socket.create_server((socket.gethostbyname(socket.gethostname()), port)), port)
