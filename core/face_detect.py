"""Detector de rostos anime usando lbpcascade_animeface.

Serve como safety net contra cenas landscape/estabelecimento: depois do
matcher, se um `video_start` não tem rosto detectável, tenta mover pra
próxima mudança de cena (dentro de ~3s) que tenha rosto.

O XML `lbpcascade_animeface.xml` é baixado uma vez do repo oficial do
nagadomi e cacheado em `%TEMP%\\ancopy\\cache\\`. Se OpenCV não estiver
instalado, o detector vira no-op (sempre retorna True).
"""
import os
import tempfile
from typing import Optional

try:
    import cv2
    _CV2 = True
except ImportError:
    _CV2 = False

try:
    import requests
    _REQUESTS = True
except ImportError:
    _REQUESTS = False


ANIMEFACE_URL = "https://raw.githubusercontent.com/nagadomi/lbpcascade_animeface/master/lbpcascade_animeface.xml"
_CACHE_DIR = os.path.join(tempfile.gettempdir(), "ancopy", "cache")
_CASCADE_FILE = os.path.join(_CACHE_DIR, "lbpcascade_animeface.xml")


def _ensure_cascade_file() -> Optional[str]:
    """Garante que o arquivo XML está em disco. Devolve o path ou None."""
    if os.path.isfile(_CASCADE_FILE) and os.path.getsize(_CASCADE_FILE) > 10000:
        return _CASCADE_FILE
    if not _REQUESTS:
        return None
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        r = requests.get(ANIMEFACE_URL, timeout=20)
        if r.status_code != 200 or len(r.content) < 10000:
            return None
        with open(_CASCADE_FILE, "wb") as f:
            f.write(r.content)
        return _CASCADE_FILE
    except Exception:
        return None


class FaceDetector:
    """Wrapper que tenta carregar animeface cascade; fallback pros haarcascades
    built-in do OpenCV se o download falhar.

    Quando `available=False`, `has_face_at()` sempre retorna True (no-op).
    """

    def __init__(self):
        self._cascade = None
        self._fallback = None
        self.available = _CV2
        self.cascade_type = "none"
        if not _CV2:
            return

        cascade_path = _ensure_cascade_file()
        if cascade_path:
            c = cv2.CascadeClassifier(cascade_path)
            if not c.empty():
                self._cascade = c
                self.cascade_type = "anime"

        # Fallback: haarcascades built-in (ruim em anime, mas melhor que nada)
        if self._cascade is None:
            for name in ("haarcascade_frontalface_default.xml",
                         "haarcascade_frontalface_alt2.xml"):
                p = os.path.join(cv2.data.haarcascades, name)
                c = cv2.CascadeClassifier(p)
                if not c.empty():
                    self._cascade = c
                    self.cascade_type = "haar (fallback — baixa precisão em anime)"
                    break

    def _detect_frame(self, cap, timestamp_seconds: float) -> int:
        """Retorna número de rostos detectados no frame no timestamp dado.
        Retorna -1 se erro ao ler."""
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(timestamp_seconds) * 1000.0)
            ret, frame = cap.read()
            if not ret or frame is None:
                return -1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            h = gray.shape[0]
            if h > 480:
                scale = 480 / h
                gray = cv2.resize(gray, (0, 0), fx=scale, fy=scale)
            # Params apertados: sf=1.1/mn=3/ms=18 evitam falsos positivos em
            # cityscapes (padrões repetitivos de janelas/placas).
            faces = self._cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=3, minSize=(18, 18),
            )
            return len(faces)
        except Exception:
            return -1

    def has_face_at(self, video_path: str, timestamp_seconds: float) -> bool:
        """True se há rosto detectável num frame do vídeo próximo ao
        `timestamp_seconds`. Amostra 2 frames (no ts e +0.4s) e exige detecção
        em pelo menos 1 deles — cascade anime às vezes erra frames isolados.

        Se OpenCV indisponível ou erro de leitura, retorna True (não intervém).
        """
        if not self.available or self._cascade is None:
            return True
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return True
            n1 = self._detect_frame(cap, timestamp_seconds)
            n2 = self._detect_frame(cap, timestamp_seconds + 0.4)
            cap.release()
        except Exception:
            return True

        # -1 = erro de leitura; trata como "não sei" (retorna True pra não mover)
        if n1 < 0 and n2 < 0:
            return True
        return n1 > 0 or n2 > 0
