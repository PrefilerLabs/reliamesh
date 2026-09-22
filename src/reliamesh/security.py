"""Keys and bounded instance-level rate limiting; never retain raw credentials."""

import hashlib
import secrets
import threading
import time
from collections import OrderedDict


def new_key() -> str:
    return "rm_" + secrets.token_urlsafe(32)


def digest_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class Limiter:
    def __init__(self, limit: int, seconds: int, capacity: int = 4096):
        self.limit, self.seconds, self.capacity = limit, seconds, capacity
        self.buckets = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, identifier: str) -> bool:
        now = time.monotonic()
        with self.lock:
            start, count = self.buckets.get(identifier, (now, 0))
            if now - start >= self.seconds:
                start, count = now, 0
            if count >= self.limit:
                return False
            self.buckets[identifier] = (start, count + 1)
            self.buckets.move_to_end(identifier)
            while len(self.buckets) > self.capacity:
                self.buckets.popitem(last=False)
            return True
