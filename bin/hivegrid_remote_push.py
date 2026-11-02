"""Web Push for hivegrid-remote: a system notification on the phone when an agent needs you, is done or hits its limit.

Opt-in (settings.json `remote.push: true`). The phone page registers a service worker (`/sw.js`), subscribes at the
browser's push service (Google, Apple, Mozilla or Microsoft, whichever the browser uses) and posts the subscription
here. A message is encrypted for that one device (RFC 8291, aes128gcm), signed with this machine's own VAPID key
(RFC 8292) and POSTed to the subscription's endpoint; the push service relays the ciphertext and cannot read it.

The crypto is the optional `cryptography` package, like PIL elsewhere in Hivegrid: without it `unavailable()` says why
and nothing is sent. Nothing here logs an endpoint (it is a bearer capability: whoever holds it can push to the device).

State is one file next to settings.json, `push.json`, mode 0600 (the VAPID private key and the subscriptions). The
server and the bot scheduler are separate processes, so every change takes a flock on `push.json.lock` and writes the
file atomically. A message that did not arrive is dropped, never retried: a late notification is worse than none.
Docs: docs/remote.md, "Notifications".
"""
import base64
import contextlib
import fcntl
import hashlib
import http.client
import json
import os
import queue
import re
import stat
import struct
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    _IMPORT_ERROR = None
except Exception as _exc:  # noqa: BLE001 - a broken install must switch the feature off, not stop the server at import
    _IMPORT_ERROR = f"web push needs the Python package 'cryptography' (apt install python3-cryptography): {type(_exc).__name__}"

MAX_SUBS = 10
MAX_LABEL = 40
MAX_ENDPOINT = 1000
MAX_PLAINTEXT = 3000          # the push services accept about 4 KB per message; a notification is far smaller
TTL = 600                     # seconds the push service may hold a message for an offline phone
POST_TIMEOUT = 5.0
QUEUE_MAX = 50               # messages waiting for a push service that hangs; more are dropped
RECORD_SIZE = 4096
VAPID_LIFETIME = 12 * 3600
DEFAULT_CONTACT = "mailto:hivegrid@example.com"

# Who may be sent to. The endpoint comes from the client, so without this list a token holder could make the server
# POST to anything it can reach (the tailnet, loopback). Only https on the default port, no credentials, no IP literal.
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com")
PUSH_SUFFIXES = (".push.apple.com", ".notify.windows.com", ".push.services.mozilla.com")

_B64 = re.compile(r"[A-Za-z0-9_-]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


def unavailable():
    """None when Web Push can work here, else the reason (a missing package)."""
    return _IMPORT_ERROR


class StoreError(Exception):
    """push.json cannot be used (damaged, not private to the user, full)."""


# ---------------------------------------------------------------- encoding and crypto

def b64e(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64d(text):
    """Strict unpadded base64url; ValueError for anything else."""
    if not isinstance(text, str) or not _B64.fullmatch(text) or len(text) % 4 == 1:
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def device_id(endpoint):
    """The short stable name of a subscription (a digest of its endpoint). The page computes the same."""
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16]


def new_vapid_private():
    """A fresh VAPID signing key as base64url of its 32-byte private value."""
    key = ec.generate_private_key(ec.SECP256R1())
    return b64e(key.private_numbers().private_value.to_bytes(32, "big"))


def _private(raw_b64):
    raw = b64d(raw_b64)
    if len(raw) != 32:
        raise ValueError("a P-256 private key is 32 bytes")
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def _public_bytes(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key_b64(private_b64):
    """The `applicationServerKey` the page subscribes with."""
    return b64e(_public_bytes(_private(private_b64)))


def encrypt(plaintext, ua_public, auth, as_private=None, salt=None):
    """The body of a Web Push request (RFC 8291 aes128gcm): header (salt, record size, our public key) and one record.
    `ua_public` is the browser's 65-byte p256dh key and `auth` its 16-byte secret. `as_private` (base64url) and `salt`
    exist for the RFC's test vector; leave them out to get a fresh key and salt. ValueError for a bad key or size."""
    if len(plaintext) > MAX_PLAINTEXT:
        raise ValueError("message too large")
    if len(auth) != 16:
        raise ValueError("auth secret is 16 bytes")
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    mine = _private(as_private) if as_private else ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(mine)
    salt = salt or os.urandom(16)
    secret = mine.exchange(ec.ECDH(), ua_key)
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_public + as_public).derive(secret)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    record = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt + struct.pack(">IB", RECORD_SIZE, len(as_public)) + as_public + record


def vapid_authorization(endpoint, private_b64, subject, now):
    """The `Authorization` header value (RFC 8292): an ES256 JWT for the endpoint's origin and our public key."""
    key = _private(private_b64)
    u = urlsplit(endpoint)
    head = b64e(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64e(json.dumps({"aud": f"https://{u.hostname}", "exp": int(now) + VAPID_LIFETIME, "sub": subject},
                             separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{claims}".encode("ascii"), ec.ECDSA(hashes.SHA256())))
    sig = b64e(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{claims}.{sig}, k={b64e(_public_bytes(key))}"


# ---------------------------------------------------------------- subscriptions

_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")


def endpoint_ok(url):
    """True for the https endpoint of a known push service, spelled the way a browser spells it (a lower-case host of
    letters, digits, dots and hyphens only)."""
    if not isinstance(url, str) or not 12 <= len(url) <= MAX_ENDPOINT or not url.isascii() or not url.isprintable() or " " in url:
        return False
    try:
        u = urlsplit(url)
        port, host = u.port, u.hostname
    except ValueError:
        return False
    if u.scheme != "https" or not host or u.username is not None or u.password is not None or u.fragment:
        return False
    if port not in (None, 443) or not _HOST.fullmatch(host) or not u.netloc.startswith(host):
        return False
    return host in PUSH_HOSTS or any(host.endswith(s) and len(host) > len(s) for s in PUSH_SUFFIXES)


def parse_subscription(body):
    """A PushSubscription (`subscription.toJSON()` plus an optional `label`) as {id, endpoint, p256dh, auth, label}.
    Raises ValueError with a short reason for anything that is not one of a known push service."""
    endpoint = body.get("endpoint")
    if not endpoint_ok(endpoint):
        raise ValueError("endpoint is not a known push service")
    keys = body.get("keys")
    if not isinstance(keys, dict) or set(keys) - {"p256dh", "auth"}:
        raise ValueError("keys must hold p256dh and auth")
    try:
        p256dh, auth = b64d(keys.get("p256dh")), b64d(keys.get("auth"))
        if len(p256dh) != 65 or p256dh[0] != 4 or len(auth) != 16:
            raise ValueError("wrong key size")
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
    except ValueError:
        raise ValueError("keys are not a valid p256dh and auth") from None
    label = body.get("label", "")
    if not isinstance(label, str):
        raise ValueError("label must be text")
    return {"id": device_id(endpoint), "endpoint": endpoint, "p256dh": keys["p256dh"], "auth": keys["auth"],
            "label": _CONTROL.sub(" ", label).strip()[:MAX_LABEL]}


def push_path(settings_file=None):
    """push.json beside settings.json (HIVEGRID_SETTINGS moves both, as in tests)."""
    from hivegrid_settings_core import settings_path
    return settings_path(settings_file).parent / "push.json"


class Store:
    """push.json: {"vapid": <private key b64url or null>, "subs": [{id, endpoint, p256dh, auth, label, owner, added}]}."""

    def __init__(self, path, clock=time.time):
        self.path = os.fspath(path)
        self.clock = clock

    # -- file handling
    @contextlib.contextmanager
    def _locked(self):
        parent = os.path.dirname(os.path.abspath(self.path))
        if not os.path.isdir(parent):
            os.makedirs(parent, mode=0o700, exist_ok=True)
        fd = os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _read(self):
        try:
            st = os.lstat(self.path)
        except FileNotFoundError:
            return {"vapid": None, "subs": []}
        except OSError as exc:
            raise StoreError(f"cannot read push.json: {exc.strerror}") from None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
            raise StoreError("push.json is not a regular file of yours")
        if st.st_mode & 0o077:
            raise StoreError("push.json is readable by others; run: chmod 600 on it")
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as f:
                doc = json.loads(f.read(1 << 20).decode("utf-8"))
        except (OSError, ValueError):
            raise StoreError("push.json is damaged; delete it to start over (devices must enable again)") from None
        vapid, subs = doc.get("vapid") if isinstance(doc, dict) else 0, doc.get("subs") if isinstance(doc, dict) else 0
        need = ("id", "endpoint", "p256dh", "auth", "label", "owner", "added")
        if not (vapid is None or isinstance(vapid, str)) or not isinstance(subs, list) or not all(
                isinstance(s, dict) and all(k in s for k in need) for s in subs):
            raise StoreError("push.json is damaged; delete it to start over (devices must enable again)")
        if vapid is not None:
            try:
                if len(b64d(vapid)) != 32:
                    raise ValueError("size")
                if not unavailable():
                    _private(vapid)          # a scalar outside the curve is no key either
            except ValueError:
                raise StoreError("push.json holds a signing key that is no key; delete it to start over (devices must enable again)") from None
        return {"vapid": vapid, "subs": subs}

    def _write(self, doc):
        parent = os.path.dirname(os.path.abspath(self.path))
        tmp = os.path.join(parent, f".push.json.{os.getpid()}.{time.time_ns()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    # -- operations
    def load(self):
        """The file as it is now; StoreError when it is unusable. Never creates anything."""
        return self._read()

    def public_key(self):
        """The VAPID public key, creating the key pair on first use."""
        with self._locked():
            doc = self._read()
            if doc["vapid"] is None:
                doc["vapid"] = new_vapid_private()
                self._write(doc)
            return public_key_b64(doc["vapid"])

    def devices(self):
        return [{"id": s["id"], "label": s["label"], "added": s["added"]} for s in self._read()["subs"]]

    def add(self, sub, owner):
        """Stores `sub` (a parse_subscription result) for `owner`, replacing the same device. StoreError when full."""
        with self._locked():
            doc = self._read()
            rest = [s for s in doc["subs"] if s["id"] != sub["id"]]
            if len(rest) >= MAX_SUBS:
                raise StoreError(f"{MAX_SUBS} devices are registered already: remove one first")
            doc["subs"] = rest + [dict(sub, owner=owner, added=int(self.clock()))]
            self._write(doc)
        return sub["id"]

    def remove(self, ids):
        """Removes the devices with these ids; returns how many were registered."""
        ids = set(ids)
        with self._locked():
            doc = self._read()
            keep = [s for s in doc["subs"] if s["id"] not in ids]
            gone = len(doc["subs"]) - len(keep)
            if gone:
                doc["subs"] = keep
                self._write(doc)
            return gone

    def sweep(self, owner):
        """Drops the devices that were registered with another token than `owner`'s (a rotation); returns how many."""
        if not os.path.lexists(self.path):
            return 0
        with self._locked():
            doc = self._read()
            keep = [s for s in doc["subs"] if s["owner"] == owner]
            gone = len(doc["subs"]) - len(keep)
            if gone:
                doc["subs"] = keep
                self._write(doc)
            return gone


# ---------------------------------------------------------------- sending

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))   # never through a proxy of the environment


def http_post(url, data, headers):
    """POST and return the HTTP status (a 4xx or 5xx too); OSError for no answer or a reply that is no HTTP. Redirects
    are never followed."""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=POST_TIMEOUT) as resp:  # noqa: S310 - endpoint_ok allowed this host
            resp.read(256)
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except http.client.HTTPException as exc:
        raise OSError(f"bad reply: {type(exc).__name__}") from None


class Pusher:
    """`Pusher(store).send_event(event)` encrypts and sends `event` to every device. `event` is {title, body, tag, url}
    (`url` a `#/...` page route the tap opens). `contact_fn()` gives the VAPID `sub` claim."""

    def __init__(self, store, contact_fn=None, post=http_post, clock=time.time, log=None):
        self.store = store
        self.contact_fn = contact_fn or (lambda: DEFAULT_CONTACT)
        self.post, self.clock = post, clock
        self.log = log or (lambda msg: None)
        self._warned = False
        self._queue = None            # made with the worker on the first send_async
        self._queue_lock = threading.Lock()

    @staticmethod
    def payload(event):
        """The JSON the service worker reads: whitelisted keys, one line each, cut short."""
        def one(v, n):
            v = _CONTROL.sub(" ", str(v or "")).strip()
            return v if len(v) <= n else v[:n - 1] + "\u2026"
        url = event.get("url") if isinstance(event.get("url"), str) else ""
        out = {"title": one(event.get("title"), 120) or "Hivegrid", "body": one(event.get("body"), 300),
               "tag": one(event.get("tag"), 60), "url": url if re.fullmatch(r"#/[A-Za-z0-9/_%.-]{0,100}", url) else "#/"}
        if event.get("test"):
            out["test"] = True
        return json.dumps(out, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def send_event(self, event, urgency="normal", only=None):
        """Sends to every device now (only to the device with id `only`, when given). Returns {"sent": n, "failed": n,
        "removed": n}; never raises (a failed round is logged as `push failed: <status or error class>`, never with an
        endpoint)."""
        result = {"sent": 0, "failed": 0, "removed": 0}
        reason = unavailable()
        if reason:
            if not self._warned:
                self._warned = True
                self.log(f"push is on but unavailable: {reason}")
            return result
        try:
            doc = self.store.load()
            if doc["vapid"] is None or not doc["subs"]:
                return result
            body = self.payload(event)
            subject = self.contact_fn() or DEFAULT_CONTACT
        except (StoreError, ValueError) as exc:
            self.log(f"push failed: {type(exc).__name__}")
            return result
        dead = []
        for sub in [s for s in doc["subs"] if only is None or s["id"] == only]:
            try:
                if not endpoint_ok(sub["endpoint"]):
                    raise ValueError("endpoint")
                headers = {"Authorization": vapid_authorization(sub["endpoint"], doc["vapid"], subject, self.clock()),
                           "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                           "TTL": str(TTL), "Urgency": urgency}
                data = encrypt(body, b64d(sub["p256dh"]), b64d(sub["auth"]))
                status = self.post(sub["endpoint"], data, headers)
            except (OSError, ValueError) as exc:
                result["failed"] += 1
                self.log(f"push failed: {type(exc).__name__}")
                continue
            if 200 <= status < 300:
                result["sent"] += 1
            else:
                result["failed"] += 1
                self.log(f"push failed: HTTP {status}")
                if status in (404, 410):
                    dead.append(sub["id"])        # the browser dropped the subscription: it can never receive again
        if dead:
            try:
                self.store.remove(dead)
                result["removed"] = len(dead)
            except (StoreError, OSError) as exc:
                self.log(f"push failed: {type(exc).__name__}")
        return result

    def send_async(self, event, urgency="normal"):
        """Queues `event` for one worker thread, so a slow push service never holds the watcher up and two messages for the
        same agent reach the push service in the order they were made (a stale one must not replace a fresh one). A full
        queue (the push service has been hanging) drops the new message: a late one is worse than none."""
        with self._queue_lock:
            if self._queue is None:
                self._queue = queue.Queue(maxsize=QUEUE_MAX)
                threading.Thread(target=self._work, name="push", daemon=True).start()
        try:
            self._queue.put_nowait((event, urgency))
        except queue.Full:
            self.log("push failed: queue full")

    def _work(self):
        while True:
            event, urgency = self._queue.get()
            try:
                self.send_event(event, urgency)
            except Exception as exc:  # noqa: BLE001 - the worker never dies of one bad message
                self.log(f"push failed: {type(exc).__name__}")


def send_from_settings(event, urgency="normal", settings_file=None, log=None):
    """For the bot scheduler (another process): send `event` with the store and contact the settings file names.
    Does nothing unless `remote.push` is true there."""
    from hivegrid_settings_core import Settings, settings_path
    path = settings_path(settings_file)
    cfg = Settings(path).remote()
    if not cfg["push"]:
        return {"sent": 0, "failed": 0, "removed": 0}
    return Pusher(Store(push_path(settings_file)), lambda: cfg["push_contact"], log=log).send_event(event, urgency)
