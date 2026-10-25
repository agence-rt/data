"""Lecture d'un fichier distant par requêtes HTTP Range (fichier « file-like » en lecture seule).

Les dalles LiDAR HD de l'IGN pèsent 100 Mo à 2 Go : le format COPC permet de ne lire que les
morceaux utiles. Le serveur de la Géoplateforme limite à ~1 requête par seconde : on regroupe donc
les lectures en gros blocs, on met en cache et on temporise.
"""
import io
import ssl
import time
import urllib.error
import urllib.request

BLOCK = 1 << 20          # 1 Mio
MIN_INTERVAL = 1.1       # secondes entre deux requêtes (limite serveur : 1/s)
MAX_RETRY = 6


class HttpRangeFile(io.RawIOBase):
    def __init__(self, url, block=BLOCK, progress=None, timeout=60):
        self.url, self.block, self.timeout, self.progress = url, block, timeout, progress
        self._pos = 0
        self._cache = {}
        self._last = 0.0
        self.requests = 0
        self.bytes_fetched = 0
        self._ctx = ssl.create_default_context()
        self.size = self._probe()

    # --- réseau -------------------------------------------------------------------------
    def _get(self, start, end):
        """Octets [start, end] inclus. Temporise et réessaie en cas de 429/5xx."""
        for attempt in range(MAX_RETRY):
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}",
                                                            "User-Agent": "AGOA-DATA/1.0"})
            try:
                self._last = time.monotonic()
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as r:
                    data = r.read()
                    cr = r.headers.get("Content-Range", "")
                self.requests += 1
                self.bytes_fetched += len(data)
                if self.progress:
                    self.progress(self.bytes_fetched, self.requests)
                return data, cr
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRY - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt < MAX_RETRY - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
        raise IOError("Échec du téléchargement partiel")

    def _probe(self):
        data, cr = self._get(0, 0)
        # Content-Range: bytes 0-0/103038114
        if "/" not in cr:
            raise IOError("Le serveur n'accepte pas les lectures partielles (Range).")
        return int(cr.rsplit("/", 1)[1])

    # --- interface fichier --------------------------------------------------------------
    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        else:
            self._pos = self.size + offset
        return self._pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self._pos
        n = max(0, min(n, self.size - self._pos))
        if n == 0:
            return b""
        first, last = self._pos // self.block, (self._pos + n - 1) // self.block
        missing = [b for b in range(first, last + 1) if b not in self._cache]
        # regroupe les blocs manquants contigus en une seule requête
        i = 0
        while i < len(missing):
            j = i
            while j + 1 < len(missing) and missing[j + 1] == missing[j] + 1:
                j += 1
            s = missing[i] * self.block
            e = min((missing[j] + 1) * self.block, self.size) - 1
            data, _ = self._get(s, e)
            for k, b in enumerate(range(missing[i], missing[j] + 1)):
                self._cache[b] = data[k * self.block:(k + 1) * self.block]
            i = j + 1
        buf = b"".join(self._cache[b] for b in range(first, last + 1))
        off = self._pos - first * self.block
        out = buf[off:off + n]
        self._pos += len(out)
        return out

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)
