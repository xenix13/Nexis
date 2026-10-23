"""Découverte automatique sur le réseau local (port UDP 7359), comme un serveur Jellyfin.

Les applications Jellyfin (Android TV notamment) envoient « who is JellyfinServer? » en diffusion ; le serveur
répond avec son adresse. Séance ne répond qu'aux adresses du réseau local, et pas plus d'une fois par
seconde à une même adresse (pas d'amplification possible depuis Internet).
"""
import ipaddress
import json
import logging
import socket
import threading
import time

log = logging.getLogger("seance.jellyfin")
PORT = 7359
MESSAGE = b"who is jellyfinserver?"


def local_ip_towards(addr):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((addr, PORT))
            return s.getsockname()[0]
    except OSError:
        return None


def is_local(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_private or ip.is_link_local or ip.is_loopback


class Discovery:
    def __init__(self, jf, http_port):
        self.jf = jf
        self.http_port = http_port
        self.sock = None
        self.last = {}
        self.stop_event = threading.Event()

    def reply_for(self, addr):
        ip = local_ip_towards(addr)
        if not ip:
            return None
        db = self.jf.db
        try:
            payload = {"Address": f"http://{ip}:{self.http_port}", "Id": self.jf.server_id(),
                       "Name": self.jf.server_name(), "EndpointAddress": None}
        finally:
            db.release()
        return json.dumps(payload).encode()

    def handle(self, data, addr):
        if data.strip().lower() != MESSAGE or not is_local(addr[0]):
            return None
        now = time.time()
        if now - self.last.get(addr[0], 0) < 1:
            return None
        if len(self.last) > 1000:
            self.last.clear()
        self.last[addr[0]] = now
        try:
            if not self.jf.enabled():
                return None
        finally:
            self.jf.db.release()
        return self.reply_for(addr[0])

    def run(self):
        while not self.stop_event.is_set():
            try:
                data, addr = self.sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                reply = self.handle(data, addr)
                if reply:
                    self.sock.sendto(reply, addr)
            except Exception:
                log.exception("Découverte Jellyfin")

    def start(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(("0.0.0.0", PORT))
            self.sock.settimeout(1)
        except OSError as e:
            log.warning("Découverte Jellyfin indisponible (port UDP %d : %s)", PORT, e)
            return False
        threading.Thread(target=self.run, daemon=True, name="jf-discovery").start()
        return True

    def stop(self):
        self.stop_event.set()
        if self.sock:
            self.sock.close()
