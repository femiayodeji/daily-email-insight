from gtts import gTTS
import pyttsx3
import tempfile
import os
import io


def use_gtts(text: str, lang: str = 'en') -> bytes:
    tts = gTTS(text=text, lang=lang)
    audio_fp = io.BytesIO()
    tts.write_to_fp(audio_fp)
    audio_fp.seek(0)
    return audio_fp.read()

def use_pyttsx3(text: str, rate: int = 200, voice: str = None) -> bytes:
    engine = pyttsx3.init()
    engine.setProperty('rate', rate)
    if voice:
        engine.setProperty('voice', voice)
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
        temp_path = tf.name
    engine.save_to_file(text, temp_path)
    engine.runAndWait()
    with open(temp_path, 'rb') as f:
        audio_bytes = f.read()
    os.remove(temp_path)
    return audio_bytes
