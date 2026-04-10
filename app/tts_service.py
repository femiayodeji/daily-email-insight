from gtts import gTTS
import io


def use_gtts(text: str, lang: str = 'en') -> bytes:
    tts = gTTS(text=text, lang=lang)
    audio_fp = io.BytesIO()
    tts.write_to_fp(audio_fp)
    audio_fp.seek(0)
    return audio_fp.read()
