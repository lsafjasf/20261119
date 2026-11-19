"""Local web server: streaming synthesis, analysis and export endpoints.

Only the Python standard library is used for HTTP. A single stateful
VowelSynthesizer feeds a ring buffer polled by the browser; parameter updates
change coefficients in place, so the stream is never restarted.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

from .analysis import lpc_envelope, magnitude_spectrum, measure_formants, voicing_ratio
from .engine import VowelSynthesizer
from .exporters import write_trajectory_csv, write_trajectory_json, write_wav
from .params import SAMPLE_RATE, VOWEL_PRESETS, SynthParams, clamp_params

WEB_DIR = Path(__file__).parent / "web"
BLOCK = 512
RING_BLOCKS = 64
ANALYSIS_BLOCKS = 16


class SynthSession:
    """Thread-safe wrapper holding the synthesizer and the latest analysis."""

    def __init__(self):
        self.lock = threading.Lock()
        self.synth = VowelSynthesizer(SynthParams(), fs=SAMPLE_RATE, block_size=BLOCK)
        self.ring = deque(maxlen=RING_BLOCKS)
        self.source_ring = deque(maxlen=ANALYSIS_BLOCKS)
        self.output_ring = deque(maxlen=ANALYSIS_BLOCKS)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self):
        period = BLOCK / SAMPLE_RATE
        while not self._stop.is_set():
            start = time.perf_counter()
            with self.lock:
                source, output = self.synth.process_block()
            pcm = np.clip(output, -1.0, 1.0)
            pcm16 = np.round(pcm * 32767).astype("<i2")
            self.ring.append(pcm16.tobytes())
            self.source_ring.append(source)
            self.output_ring.append(output)
            elapsed = time.perf_counter() - start
            time.sleep(max(0.0, period - elapsed))

    def update_params(self, changes: dict):
        with self.lock:
            current = self.synth.params
            allowed = SynthParams().__dict__.keys()
            parsed = {k: float(v) for k, v in changes.items() if k in allowed}
            self.synth.params = clamp_params(current.updated(**parsed))
            return self.synth.params

    def set_preset(self, name: str):
        preset = VOWEL_PRESETS.get(name)
        if preset is None:
            return None
        with self.lock:
            tau = self.synth.params.smooth_tau
            self.synth.params = clamp_params(preset.updated(smooth_tau=tau))
            return self.synth.params

    def drain_audio(self) -> bytes:
        chunks = []
        while self.ring:
            chunks.append(self.ring.popleft())
        return b"".join(chunks)

    def analysis_snapshot(self):
        with self.lock:
            if not self.output_ring:
                return None
            source = np.concatenate(list(self.source_ring))
            output = np.concatenate(list(self.output_ring))
            applied = dict(self.synth._smooth)
        freqs, src_db = magnitude_spectrum(source, SAMPLE_RATE)
        env_freqs, env_db = lpc_envelope(output, SAMPLE_RATE, order=22)
        _, out_db = magnitude_spectrum(output, SAMPLE_RATE)
        declared = [
            (applied["f1"], applied["bw1"]),
            (applied["f2"], applied["bw2"]),
            (applied["f3"], applied["bw3"]),
        ]
        formants = measure_formants(output, SAMPLE_RATE, declared)
        voicing = voicing_ratio(source, SAMPLE_RATE, applied["f0"])
        step = max(1, freqs.shape[0] // 256)
        return {
            "freqs": freqs[::step].round(1).tolist(),
            "source_db": src_db[::step].round(1).tolist(),
            "output_db": out_db[::step].round(1).tolist(),
            "env_freqs": env_freqs[::step].round(1).tolist(),
            "env_db": env_db[::step].round(1).tolist(),
            "formants": formants,
            "voicing": voicing,
            "applied": {k: round(v, 3) for k, v in applied.items()},
        }

    def export(self, duration: float, keyframes=None):
        with self.lock:
            params = self.synth.params
        if keyframes:
            target = [(t, SynthParams(**p)) for t, p in keyframes]
        else:
            target = params
        from .exporters import render
        audio, trajectory = render(target, duration=duration, fs=SAMPLE_RATE,
                                   block_size=BLOCK)
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        wav_path = Path("artifacts") / f"vowel-{stamp}.wav"
        csv_path = Path("artifacts") / f"trajectory-{stamp}.csv"
        json_path = Path("artifacts") / f"trajectory-{stamp}.json"
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        write_wav(str(wav_path), audio, SAMPLE_RATE)
        write_trajectory_csv(str(csv_path), trajectory)
        write_trajectory_json(str(json_path), trajectory,
                              {"fs": SAMPLE_RATE, "duration": duration})
        return {"wav": str(wav_path), "csv": str(csv_path),
                "json": str(json_path), "samples": int(audio.shape[0])}

    def stop(self):
        self._stop.set()


def make_handler(session: SynthSession):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def _send(self, code, body, content_type="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlparse(self.path).path
            if route in ("/", "/index.html"):
                return self._serve("index.html", "text/html")
            if route == "/app.js":
                return self._serve("app.js", "application/javascript")
            if route == "/style.css":
                return self._serve("style.css", "text/css")
            if route == "/api/stream":
                audio = session.drain_audio()
                return self._send(200, audio, "audio/pcm")
            if route == "/api/state":
                return self._send(200, session.analysis_snapshot() or {})
            if route == "/api/params":
                with session.lock:
                    return self._send(200, session.synth.params.to_dict())
            return self._send(404, {"error": "not found"})

        def _serve(self, name, content_type):
            path = WEB_DIR / name
            if not path.exists():
                return self._send(404, {"error": "missing asset"})
            self._send(200, path.read_bytes(), content_type)

        def do_POST(self):
            route = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "invalid json"})
            if route == "/api/params":
                return self._send(200, session.update_params(payload).to_dict())
            if route == "/api/preset":
                params = session.set_preset(payload.get("name", ""))
                if params is None:
                    return self._send(404, {"error": "unknown preset"})
                return self._send(200, params.to_dict())
            if route == "/api/export":
                result = session.export(
                    float(payload.get("duration", 2.0)),
                    payload.get("keyframes"))
                return self._send(200, result)
            return self._send(404, {"error": "not found"})

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8000):
    session = SynthSession()
    httpd = ThreadingHTTPServer((host, port), make_handler(session))
    print(f"Synthesizer UI: http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()
        httpd.server_close()


if __name__ == "__main__":
    serve()
