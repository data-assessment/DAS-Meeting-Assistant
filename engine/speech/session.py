"""Two independent Azure Speech sessions; raw results remain in RAM."""
import re
import threading
import time
import uuid
from .core import AudioBuffer, PcmReader, Transcript
from engine.notes_i18n import t
from engine.stt_context import dictionary_terms


def apply_phrase_list(sdk, recognizer, terms=None):
    """Bias recognition towards the business dictionary (exact names, products, jargon).
    Best effort: an unsupported phrase list must never keep a meeting from being captured.
    Returns False when the dictionary could not be applied, so the caller can tell the user."""
    terms = dictionary_terms() if terms is None else terms
    if not terms:
        return True
    try:
        grammar = sdk.PhraseListGrammar.from_recognizer(recognizer)
        for term in terms:
            grammar.addPhrase(term)
        return True
    except Exception as exc:
        print("[speech] business dictionary not applied:", type(exc).__name__)
        return False

def validate(region, key, language, local_name):
    if not re.fullmatch(r"[a-z][a-z0-9]{1,39}", region):
        raise ValueError(t("speech.errors.region"))
    if not key.strip() or any(ord(c) < 32 for c in key):
        raise ValueError(t("speech.errors.key"))
    validate_input(language, local_name)

def validate_input(language, local_name):
    if not re.fullmatch(r"[a-z]{2,3}-[A-Za-z]{2,4}", language):
        raise ValueError(t("speech.errors.language"))
    if not local_name.strip() or len(local_name) > 80 or any(ord(c) < 32 for c in local_name):
        raise ValueError(t("speech.errors.localName"))

def wait_future(future, timeout, cancel=None):
    done = threading.Event()
    result = []
    def wait():
        try:
            future.get()
            result.append(True)
        except Exception:
            result.append(False)
        finally:
            done.set()
    threading.Thread(target=wait, daemon=True).start()
    deadline = time.monotonic() + timeout
    while not done.wait(0.1):
        if time.monotonic() >= deadline or (cancel and cancel.is_set()):
            raise TimeoutError("Azure operation did not complete")
    if not result[0]:
        raise RuntimeError("Azure operation failed")

class Session:
    TEST_SECONDS = 20 * 60

    def transcript_source(self, source):
        return source

    def __init__(self, local_name, sdk_module=None, capture_factory=None):
        self._sdk = sdk_module
        self._capture_factory = capture_factory
        self.id = str(uuid.uuid4())
        self.transcript = Transcript(self.id, local_name)
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.phase = "Bereit"
        self.error = ""
        # Shown, not fatal: the meeting is captured, only without the business dictionary.
        self.dictionary_warning = ""
        self.buffers = {}
        self.capture = None
        self._lock = threading.Lock()
        self._credential_provider = None
        self._credential = None
        self._refresh_at = 0
        self._token_credential = None
        self._endpoint = ""

    def start_entra(self, endpoint, language, selection, credential):
        if not re.fullmatch(r"https://[A-Za-z0-9-]+\.cognitiveservices\.azure\.com/?", endpoint):
            raise ValueError(t("speech.errors.dasNotConfigured"))
        validate_input(language, self.transcript.local_name)
        from engine.ai_auth import AZURE_SCOPE
        credential.get_token(AZURE_SCOPE)  # Fail before opening capture if sign-in is missing.
        self._token_credential, self._endpoint = credential, endpoint
        try:
            self._start(None, None, language, selection)
        except Exception:
            self._token_credential = None
            raise

    def _speech_config(self, sdk, region, key):
        if self._token_credential:
            return sdk.SpeechConfig(token_credential=self._token_credential, endpoint=self._endpoint)
        return (sdk.SpeechConfig(auth_token=key, region=region) if self._credential
                else sdk.SpeechConfig(subscription=key, region=region))

    def start_managed(self, language, selection, credential_provider):
        credential = credential_provider()
        self._credential_provider = credential_provider
        self._credential = credential
        self._refresh_at = credential.expires - 120
        try:
            self.start(credential.region, credential.token, language, selection)
        except Exception:
            self._credential = self._credential_provider = None
            raise

    def _refresh_credentials(self, recognizers):
        if not self._credential or time.monotonic() < self._refresh_at:
            return
        from engine.notes_cloud import AccessDenied
        try:
            fresh = self._credential_provider()
            if self.cancel.is_set():
                return
            if fresh.region != self._credential.region:
                raise AccessDenied(t("speech.errors.dasRegionChanged"))
            for _, recognizer in recognizers:
                try:
                    recognizer.authorization_token = fresh.token
                except AttributeError:
                    # Speech SDK 1.51.2 ConversationTranscriber assigns through
                    # super().authorization_token, which raises in Python. Use
                    # the public property collection, exactly as Recognizer's
                    # base setter does; changing SpeechConfig would not update
                    # the already running recognizer.
                    from azure.cognitiveservices.speech import PropertyId
                    recognizer.properties.set_property(
                        PropertyId.SpeechServiceAuthorization_Token, fresh.token)
            self._credential = fresh
            self._refresh_at = fresh.expires - 120
        except AccessDenied:
            self.fail(t("speech.errors.dasAccessExpired"))
        except Exception:
            if time.monotonic() >= self._credential.expires - 30:
                self.fail(t("speech.errors.dasRenewFailed"))
            else:
                self._refresh_at = time.monotonic() + 10

    def fail(self, message):
        with self._lock:
            if not self.error:
                self.error = message
        self.cancel.set()

    def start(self, region, key, language, selection):
        validate(region, key, language, self.transcript.local_name)
        self._start(region, key, language, selection)

    def _start(self, region, key, language, selection):
        if set(selection) != {"mic", "loopback"}:
            raise ValueError(t("speech.errors.selectDevices"))
        with self._lock:
            if self.phase != "Bereit":
                raise ValueError(t("speech.errors.alreadyStarted"))
            self.phase = "Verbindung wird aufgebaut"
        threading.Thread(target=self._run, args=(region, key, language, selection), daemon=True).start()

    def stop(self):
        self.cancel.set()

    def _run(self, region, key, language, selection):
        recognizers, readers, callbacks, streams, ended, disconnect = [], [], [], [], [], []
        self.phase = "Verbindung wird aufgebaut"
        try:
            if self._sdk is None:
                import azure.cognitiveservices.speech as sdk
            else:
                sdk = self._sdk
            if self._capture_factory is None:
                from .capture import Capture
            else:
                Capture = self._capture_factory
            for source, device in selection.items():
                rate, channels = int(device["defaultSampleRate"]), int(device["maxInputChannels"])
                if not 8000 <= rate <= 192000 or not 1 <= channels <= 8:
                    raise ValueError("Unsupported capture format")
                buffer = AudioBuffer(rate * channels * 4 * 3, self.fail)
                self.buffers[source] = buffer
                reader = PcmReader(buffer, rate, channels)
                readers.append(reader)
                class Pull(sdk.audio.PullAudioInputStreamCallback):
                    def __init__(self, reader):
                        super().__init__()
                        self.reader = reader
                    def read(self, target):
                        try:
                            return self.reader.read(target)
                        except Exception:
                            self.reader.buffer.on_error(t("speech.errors.audioProcessingFailed"))
                            self.reader.close()
                            return 0
                    def close(self):
                        self.reader.close()
                callback = Pull(reader)
                callbacks.append(callback)
                stream = sdk.audio.PullAudioInputStream(callback,
                    sdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1))
                streams.append(stream)
                config = self._speech_config(sdk, region, key)
                config.speech_recognition_language = language
                config.set_property(sdk.PropertyId.SpeechServiceResponse_DiarizeIntermediateResults, "true")
                config.set_property(sdk.PropertyId.SpeechServiceConnection_EnableAudioLogging, "false")
                config.set_property(sdk.PropertyId.Speech_LogFilename, "")
                audio_config = sdk.audio.AudioConfig(stream=stream)
                if source == "loopback":
                    recognizer = sdk.transcription.ConversationTranscriber(speech_config=config, audio_config=audio_config)
                    final_signal, partial_signal = recognizer.transcribed, recognizer.transcribing
                else:
                    recognizer = sdk.SpeechRecognizer(speech_config=config, audio_config=audio_config)
                    final_signal, partial_signal = recognizer.recognized, recognizer.recognizing
                if not apply_phrase_list(sdk, recognizer):
                    self.dictionary_warning = t("speech.notices.dictionaryNotApplied")
                def receive(event, src=source, final=True):
                    if event.result.reason not in (sdk.ResultReason.RecognizedSpeech, sdk.ResultReason.RecognizingSpeech):
                        return
                    try:
                        self.transcript.add(self.transcript_source(src), getattr(event.result, "speaker_id", None),
                            event.result.text, event.result.offset / 10_000_000, final)
                    except Exception:
                        self.fail(t("speech.errors.transcriptLimitOrProcessing"))
                final_signal.connect(receive)
                partial_signal.connect(lambda event, handler=receive: handler(event, final=False))
                completed = threading.Event()
                ended.append(completed)
                def stopped(event, completed=completed):
                    completed.set()
                    if not self.cancel.is_set():
                        self.fail(t("speech.errors.azureEndedEarly"))
                def canceled(event, src=source):
                    # EndOfStream during our normal stop is expected, not an error.
                    if self.cancel.is_set() and event.reason != sdk.CancellationReason.Error:
                        return
                    # Never log raw event/error_details: they can contain service/request data.
                    device = t("speech.sources.microphone") if src == "mic" else t("speech.sources.playback")
                    self.fail(t("speech.errors.azureSourceManaged", source=device) if self._token_credential
                              else t("speech.errors.azureSourceKey", source=device))
                recognizer.session_stopped.connect(stopped)
                recognizer.canceled.connect(canceled)
                recognizers.append((source, recognizer))
                disconnect.extend([final_signal, partial_signal, recognizer.session_stopped, recognizer.canceled])
            key = None
            self.capture = Capture(selection, self.buffers, self.fail)
            self.capture.open()
            starts = [(r.start_transcribing_async() if source == "loopback" else r.start_continuous_recognition_async())
                      for source, r in recognizers]
            self.capture.start()
            for future in starts:
                wait_future(future, 15, self.cancel)
            self.phase = "Läuft"
            started = time.monotonic()
            while not self.cancel.wait(0.2):
                self._refresh_credentials(recognizers)
                if hasattr(self.capture, "healthy") and not self.capture.healthy():
                    self.fail(t("speech.errors.deviceInactive"))
                if time.monotonic() - started > self.TEST_SECONDS:
                    self.fail(t("speech.errors.testLimit", minutes=self.TEST_SECONDS // 60))
        except Exception:
            if not self.cancel.is_set():
                self.fail(t("speech.errors.startFailed"))
        finally:
            self.phase = "Wird beendet"
            self.cancel.set()
            if self.capture is not None:
                try:
                    self.capture.close()
                except Exception:
                    self.fail(t("speech.errors.devicesCloseFailed"))
            for buffer in self.buffers.values():
                if self.error:
                    buffer.discard()
                else:
                    buffer.finish()
            # EOF first, then wait for final recognition events. No cancel-before-drain loss.
            deadline = time.monotonic() + (0 if self.error else 8)
            for event in ended:
                event.wait(max(0, deadline - time.monotonic()))
            if ended and not self.error and not all(event.is_set() for event in ended):
                self.fail(t("speech.errors.completionUnconfirmed"))
            for source, recognizer in recognizers:
                try:
                    future = (recognizer.stop_transcribing_async() if source == "loopback"
                              else recognizer.stop_continuous_recognition_async())
                    wait_future(future, 3)
                except Exception:
                    self.fail(t("speech.errors.sessionEndTimeout"))
            for reader in readers:
                reader.close()
            for signal in disconnect:
                try:
                    signal.disconnect_all()
                except Exception:
                    pass
            recognizers.clear()
            streams.clear()
            callbacks.clear()
            self.transcript.seal()
            self._credential = self._credential_provider = None
            self._token_credential = None
            self.phase = "Beendet" if not self.error else "Beendet mit Hinweis"
            self.finished.set()

    def snapshot(self):
        return {"phase": self.phase, "error": self.error,
                "buffers": {k: (v.size, v.capacity) for k, v in self.buffers.copy().items()},
                "metrics": self.capture.metrics.copy() if self.capture else {}}
