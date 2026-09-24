"""Bounded three-minute MAI comparison. No filesystem audio or transcript storage."""
import io
import json
import math
import threading
import time
import wave
import requests
from .core import AudioBuffer, PcmReader
from .session import Session, validate

MAX_SECONDS = 180
PCM_RATE = 32000
MAX_RESPONSE = 2 * 1024 * 1024


class MaiError(Exception):
    pass


def transcribe(pcm, region, key, diarize, abort):
    """One synchronous multipart request from RAM, no retries or redirects."""
    if region != "northeurope":
        raise MaiError("MAI-Test benötigt die Region northeurope und deren Speech-Schlüssel.")
    if not pcm or len(pcm) > MAX_SECONDS * PCM_RATE or len(pcm) % 2:
        raise MaiError("MAI-Audiopuffer ungültig oder Testlimit überschritten.")
    if abort.is_set():
        return []
    definition = {"enhancedMode": {"enabled": True, "model": "MAI-Transcribe-2",
        "modelOptions": {"timestamps": "word", "transcribeStyle": "verbatim"}},
        "diarization": {"enabled": bool(diarize)}}
    # Automatic language detection: language switching remains possible.
    try:
        with io.BytesIO() as wav, requests.Session() as client:
            client.trust_env = False  # No implicit netrc credentials or environment proxy.
            with wave.open(wav, "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(16000)
                writer.writeframes(pcm)
            wav.seek(0)
            if abort.is_set():
                return []
            with client.post(
                "https://northeurope.api.cognitive.microsoft.com/speechtotext/transcriptions:transcribe?api-version=2025-10-15",
                headers={"Ocp-Apim-Subscription-Key": key},
                files={"audio": ("audio.wav", wav, "audio/wav")},
                data={"definition": json.dumps(definition)},
                timeout=(10, 60), allow_redirects=False, stream=True,
            ) as response:
                if response.status_code != 200:
                    hints = {401: "Schlüssel der North-Europe-Ressource prüfen.",
                             403: "Berechtigung und Netzwerkzugriff prüfen.",
                             404: "MAI-Verfügbarkeit und Endpunkt prüfen.",
                             408: "MAI-Zeitlimit; bitte kürzeren Test verwenden.",
                             429: "Azure-Kontingent erreicht; später erneut testen."}
                    raise MaiError(f"MAI HTTP {response.status_code}. " + hints.get(response.status_code,
                        "MAI-Anfrage fehlgeschlagen; keine automatische Wiederholung."))
                body = bytearray()
                deadline = time.monotonic() + 60
                for chunk in response.iter_content(8192):
                    if abort.is_set():
                        return []
                    if len(body) + len(chunk) > MAX_RESPONSE or time.monotonic() > deadline:
                        raise MaiError("MAI-Antwort überschreitet das Größen- oder Zeitlimit.")
                    body.extend(chunk)
                if abort.is_set():
                    return []
                data = json.loads(body)
                body.clear()
        phrases = data.get("phrases")
        if not isinstance(phrases, list) or len(phrases) > 1000:
            raise MaiError("MAI-Antwort hat ein unerwartetes Format.")
        result = []
        for phrase in phrases:
            text = phrase.get("text", "")
            offset = phrase.get("offsetMilliseconds", 0) / 1000
            speaker = phrase.get("speaker")
            if not isinstance(text, str) or len(text) > 4096 or not math.isfinite(offset) or offset < 0:
                raise MaiError("MAI-Antwort enthält ungültige Text- oder Zeitdaten.")
            if speaker is not None and (not isinstance(speaker, int) or isinstance(speaker, bool) or speaker < 0):
                raise MaiError("MAI-Antwort enthält eine unerwartete Sprecherkennung.")
            if text.strip():
                result.append((speaker, text, offset))
        return result
    except MaiError:
        raise
    except Exception:
        raise MaiError("MAI-Verbindung oder Antwortverarbeitung fehlgeschlagen. Netzwerk und Speech-Zugang prüfen.") from None


class MaiSession(Session):
    def __init__(self, local_name, capture_factory=None, transport=transcribe):
        super().__init__(local_name, capture_factory=capture_factory)
        self.transport = transport
        self.abort = threading.Event()
        self.pcm = {}
        self.seconds = 0
        self.uploads = 0

    def start(self, region, key, language, selection):
        validate(region, key, language, self.transcript.local_name)
        if region != "northeurope":
            raise ValueError("Für MAI northeurope und den Schlüssel dieser Ressource verwenden.")
        super().start(region, key, language, selection)

    def discard(self):
        # Prevent any further upload and reject results of an already running request.
        self.abort.set()
        self.stop()
        self.transcript.clear()

    def _collect(self, source, reader):
        target = memoryview(bytearray(8192))
        try:
            while not self.abort.is_set():
                count = reader.read(target)
                if not count:
                    break
                if len(self.pcm[source]) + count > MAX_SECONDS * PCM_RATE:
                    self.fail("MAI-RAM-Limit erreicht; Aufnahme verworfen.")
                    break
                self.pcm[source].extend(target[:count])
        except Exception:
            self.fail("MAI-Audioverarbeitung fehlgeschlagen.")

    def _run(self, region, key, language, selection):
        readers, workers = [], []
        try:
            if self._capture_factory is None:
                from .capture import Capture
            else:
                Capture = self._capture_factory
            for source, device in selection.items():
                rate, channels = int(device["defaultSampleRate"]), int(device["maxInputChannels"])
                reader = PcmReader(AudioBuffer(rate * channels * 4 * 3, self.fail), rate, channels)
                readers.append(reader)
                self.buffers[source] = reader.buffer
                self.pcm[source] = bytearray()
                worker = threading.Thread(target=self._collect, args=(source, reader), daemon=True)
                workers.append(worker)
                worker.start()
            self.capture = Capture(selection, self.buffers, self.fail)
            self.capture.open()
            self.capture.start()
            started = time.monotonic()
            self.phase = "MAI: Aufnahme läuft; Ergebnis nach Stop (max. 3 Minuten)"
            while not self.cancel.wait(.1):
                self.seconds = time.monotonic() - started
                if not self.capture.healthy():
                    self.fail("Audiogerät nicht mehr aktiv; MAI-Test wird beendet.")
                # Leave room for the last callback and resampler drain at the RAM cap.
                if self.seconds >= MAX_SECONDS - 1:
                    self.stop()
            self.capture.close()
            for reader in readers:
                reader.buffer.finish()
            for worker in workers:
                worker.join(5)
            if any(worker.is_alive() for worker in workers):
                raise MaiError("MAI-Audioabschluss nicht rechtzeitig bestätigt.")
            if self.error or self.abort.is_set():
                return
            for source in ("mic", "loopback"):
                if self.abort.is_set():
                    break
                if not self.pcm[source]:
                    raise MaiError("Keine Audiodaten bei " + ("Mikrofon" if source == "mic" else "Wiedergabe") + ". Geräteauswahl prüfen.")
                self.phase = "MAI verarbeitet " + ("Mikrofon" if source == "mic" else "Wiedergabe") + " …"
                results = self.transport(self.pcm[source], region, key, source == "loopback", self.abort)
                self.pcm[source].clear()
                if self.abort.is_set():
                    break
                self.uploads += 1
                for speaker, text, offset in results:
                    # Preserve numeric speaker 0. IDs apply only to this one source/request.
                    label = f"MAI-Sprecher {speaker}" if speaker is not None else "MAI-Unbekannt"
                    self.transcript.add(source, label, text, offset)
                results.clear()
        except MaiError as exc:
            self.fail(str(exc))
        except Exception:
            self.fail("MAI-Test fehlgeschlagen. Geräte, Region und Speech-Zugang prüfen.")
        finally:
            key = None
            self.cancel.set()
            if self.capture is not None:
                try:
                    self.capture.close()
                except Exception:
                    self.fail("Audiogeräte konnten nicht vollständig geschlossen werden.")
            for reader in readers:
                reader.close()
            for worker in workers:
                worker.join(5)
            for pcm in self.pcm.values():
                pcm.clear()
            self.transcript.seal()
            self.phase = ("Verworfen" if self.abort.is_set() else
                          "Beendet mit Hinweis" if self.error else
                          f"MAI beendet: {self.uploads} Spuren verarbeitet")
            self.finished.set()

    def snapshot(self):
        state = super().snapshot()
        state["phase"] = self.phase + (f" – {self.seconds:.0f} s" if self.phase.startswith("MAI: Aufnahme") else "")
        return state
