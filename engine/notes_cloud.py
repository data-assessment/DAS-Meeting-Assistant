"""DAS notes access. Resource keys never enter the managed client."""
from dataclasses import dataclass, field
import re
import time

import httpx
from openai import AzureOpenAI

import config
from engine.ai_auth import gateway_token


class AccessDenied(RuntimeError):
    pass


class ServiceUnavailable(RuntimeError):
    pass


@dataclass
class SpeechCredential:
    region: str
    token: str = field(repr=False)
    expires: float


def speech_credential() -> SpeechCredential:
    """Obtain a fresh regional Speech token; keep it only in the capture session."""
    started = time.monotonic()
    try:
        token = gateway_token(interactive=False)
    except Exception:
        raise AccessDenied("Bitte in den Einstellungen mit Microsoft anmelden.") from None
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=15) as client:
            response = client.post(config.USAGE_SERVICE_ENDPOINT.rstrip('/') + '/speech/session',
                                   headers={"Authorization": "Bearer " + token}, json={})
        if response.status_code in (401, 403):
            raise AccessDenied("DAS-Zugang fehlt oder ist abgelaufen. Bitte erneut anmelden; gegebenenfalls muss DAS Ihre Organisation freischalten.")
        if response.status_code in (404, 503):
            raise ServiceUnavailable("Der DAS-Sprachdienst ist noch nicht bereit. Bitte an den DAS-Support wenden.")
        if response.status_code != 200:
            raise ServiceUnavailable("Der DAS-Sprachdienst ist vorübergehend nicht erreichbar.")
        data = response.json()
        region, value, ttl = data.get('region'), data.get('authorizationToken'), data.get('expiresIn')
        if (not isinstance(region, str) or not re.fullmatch(r'[a-z][a-z0-9]{1,39}', region)
                or not isinstance(value, str) or not 20 <= len(value) <= 16384
                or any(ord(c) < 33 for c in value)
                or type(ttl) is not int or not 180 <= ttl <= 600):
            raise ValueError()
        return SpeechCredential(region, value, started + ttl)
    except (AccessDenied, ServiceUnavailable):
        raise
    except Exception:
        raise ServiceUnavailable("DAS-Sprachzugang konnte nicht geladen werden. Verbindung prüfen und erneut versuchen.") from None


def summary_client():
    from engine.ai_auth import azure_token
    direct = config.AI_MODE == "entra"
    return AzureOpenAI(azure_endpoint=config.AOAI_ENDPOINT if direct else config.AI_GATEWAY_ENDPOINT,
                       api_version=config.AOAI_API_VERSION,
                       azure_ad_token_provider=azure_token if direct else gateway_token, max_retries=0,
                       timeout=httpx.Timeout(180, connect=10, write=30, pool=10),
                       http_client=httpx.Client(trust_env=False, follow_redirects=False))


def summary_model():
    if not config.SUMMARY_MODELS:
        raise RuntimeError("DAS-Zusammenfassungen sind noch nicht konfiguriert.")
    return config.SUMMARY_MODELS[0]


def check_access():
    """Explicit setup check, using only synthetic silence and text."""
    if config.AI_MODE == "entra":
        check_speech_access()
    else:
        speech_credential()
    try:
        with summary_client() as client:
            result = client.chat.completions.create(model=summary_model(),
                messages=[{"role": "user", "content": "Antworte mit OK."}],
                max_completion_tokens=256, store=False)
        if not result.choices:
            raise ValueError()
    except Exception:
        raise RuntimeError("DAS-Zusammenfassungen sind nicht erreichbar oder für Ihr Konto nicht freigeschaltet. Bitte an den DAS-Support wenden.") from None


def check_speech_access():
    """Test the same SDK, endpoint and diarization service as meetings, with no microphone."""
    import threading
    import azure.cognitiveservices.speech as sdk
    from engine.ai_auth import UserTokenCredential
    from engine.speech.session import wait_future

    stream = sdk.audio.PushAudioInputStream()
    transcriber = None
    connected, ended, failed = threading.Event(), threading.Event(), threading.Event()
    try:
        speech = sdk.SpeechConfig(endpoint=config.SPEECH_ENDPOINT, token_credential=UserTokenCredential())
        speech.speech_recognition_language = "de-DE"
        speech.set_property(sdk.PropertyId.SpeechServiceConnection_EnableAudioLogging, "false")
        transcriber = sdk.transcription.ConversationTranscriber(
            speech_config=speech, audio_config=sdk.audio.AudioConfig(stream=stream))
        transcriber.session_started.connect(lambda _: connected.set())
        transcriber.session_stopped.connect(lambda _: ended.set())
        def canceled(event):
            if event.reason == sdk.CancellationReason.Error:
                failed.set()
            ended.set()
        transcriber.canceled.connect(canceled)
        wait_future(transcriber.start_transcribing_async(), 15)
        stream.write(bytes(3200))  # 100 ms of synthetic silence, only in RAM.
        stream.close()
        # session_started alone is not proof: an authentication error can arrive
        # afterwards. Let Azure process EOF and check its final outcome.
        if not ended.wait(15) or not connected.is_set() or failed.is_set():
            raise RuntimeError()
    except Exception:
        raise RuntimeError("DAS-Spracherkennung ist nicht erreichbar oder für Ihr Firmenkonto nicht freigeschaltet. Bitte an den DAS-Support wenden.") from None
    finally:
        stream.close()
        if transcriber is not None:
            try:
                wait_future(transcriber.stop_transcribing_async(), 3)
            except Exception:
                pass
            transcriber.session_started.disconnect_all()
            transcriber.session_stopped.disconnect_all()
            transcriber.canceled.disconnect_all()
