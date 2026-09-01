import os
import re
import base64
import logging
import asyncio
import httpx

logger = logging.getLogger(__name__)

SARVAM_API_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_MAX_CHARS = 2500

class AudioGenerationError(Exception):
    """Custom exception raised when TTS audio generation fails."""
    pass

def _split_text(text: str, max_chars: int = SARVAM_MAX_CHARS) -> list[str]:
    """
    Splits slide script text into chunks at sentence boundaries 
    to fit within Sarvam's API character limits.
    """
    if len(text) <= max_chars:
        return [text]

    chunks = []
    current = ""
    # Split on sentence boundaries
    sentences = re.split(r'(?<=[.?!])\s+', text)

    for sentence in sentences:
        if len(sentence) > max_chars:
            while len(sentence) > max_chars:
                chunks.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            current = sentence
            continue

        if len(current) + len(sentence) + 1 > max_chars:
            if current:
                chunks.append(current.strip())
            current = sentence
        else:
            current = (current + " " + sentence).strip() if current else sentence

    if current:
        chunks.append(current.strip())

    return chunks

async def _generate_single_slide_audio(
    slide: dict,
    slide_idx: int,
    total_slides: int,
    lesson_dir: str,
    lesson_id: str,
    api_key: str,
    client: httpx.AsyncClient,
    progress_callback=None
) -> str:
    """
    Generates narration audio for a single scene asynchronously.
    Strictly binds scene_no and scene_{slide_no}.wav to guarantee scene mapping.
    """
    import time
    dummy_wav_b64 = "UklGRigAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQQAAAAAAA=="
    
    slide_no = slide.get("scene_no", slide_idx)
    audio_url = slide.get("audio_url")
    if audio_url and audio_url.startswith("http"):
        logger.info(f"[AudioGen] Slide {slide_no} already has audio URL: {audio_url}")
        print(f"🚀 [AudioGen] Skip TTS. Using pre-generated audio for Scene {slide_no} -> {audio_url}", flush=True)
        if progress_callback:
            await progress_callback(slide_no, total_slides, audio_url)
        return audio_url

    if not isinstance(slide_no, int):
        slide_no = slide_idx
        
    text = slide.get("teacher_script", "").strip()
    if not text:
        text = f"Scene {slide_no}."
        
    wav_filename = f"scene_{slide_no}.wav"
    wav_path = os.path.join(lesson_dir, wav_filename)
    
    # Soft fallback for local development if API key is missing
    if not api_key:
        logger.warning(f"[AudioGen] SARVAM_API_KEY not configured. Saving mock WAV for scene {slide_no}.")
        print(f"[NOTICE] [AudioGen] SARVAM_API_KEY missing. Saving mock silent audio for scene {slide_no}.", flush=True)
        with open(wav_path, "wb") as f:
            f.write(base64.b64decode(dummy_wav_b64))
        fallback_url = f"/uploads/visual_lessons/{lesson_id}/{wav_filename}"
        if progress_callback:
            await progress_callback(slide_no, total_slides, fallback_url)
        return fallback_url
        
    try:
        from backend.app.services.chat.tts_service import synthesize_text_cached
        # Timed for pipeline-logs observability (2026-09-02, per explicit
        # request) - this is the actual Sarvam API round-trip, the number
        # that answers "how long is the server TTS taking per clip".
        tts_started = time.time()
        all_audio_bytes, _ = await synthesize_text_cached(
            text=text,
            language="en-IN",
            speaker="ritu",
            model="bulbul:v3"
        )
        tts_duration_ms = round((time.time() - tts_started) * 1000)

        with open(wav_path, "wb") as f:
            f.write(all_audio_bytes)

        from backend.app.core.supabase_storage import upload_file_to_supabase
        # upload_file_to_supabase uses a blocking httpx.Client, not the async
        # one - calling it directly here would freeze the whole event loop for
        # its duration, which silently serializes every "concurrent" scene in
        # generate_slide_audio's asyncio.gather() and defeats per-scene
        # streaming upstream. Run it on a thread so scenes actually overlap.
        cloud_audio_url = await asyncio.to_thread(upload_file_to_supabase, wav_path, f"{lesson_id}/{wav_filename}")
        final_audio_url = cloud_audio_url or f"/uploads/visual_lessons/{lesson_id}/{wav_filename}"

        logger.info(f"[RENDER LOG] [AUDIO TTS SUCCESS] Scene {slide_no} audio ready ({len(all_audio_bytes)} bytes, {tts_duration_ms} ms) -> {final_audio_url}")
        print(f"[RENDER LOG] [AUDIO TTS SUCCESS] Scene {slide_no} audio ready -> {final_audio_url}", flush=True)

        if progress_callback:
            await progress_callback(slide_no, total_slides, final_audio_url, tts_duration_ms)

        return final_audio_url

    except Exception as e:
        logger.warning(f"[AudioGen] Fallback audio for Scene {slide_no}: {e}")
        print(f"[RENDER LOG] [AUDIO TTS NOTICE] Scene {slide_no} using silent fallback audio: {e}", flush=True)
        try:
            with open(wav_path, "wb") as f:
                f.write(base64.b64decode(dummy_wav_b64))
            from backend.app.core.supabase_storage import upload_file_to_supabase
            cloud_audio_url = await asyncio.to_thread(upload_file_to_supabase, wav_path, f"{lesson_id}/{wav_filename}")
            final_audio_url = cloud_audio_url or f"/uploads/visual_lessons/{lesson_id}/{wav_filename}"
            if progress_callback:
                await progress_callback(slide_no, total_slides, final_audio_url)
            return final_audio_url
        except Exception as write_err:
            logger.error(f"[AudioGen] Failed writing fallback audio for Scene {slide_no}: {write_err}")
            if progress_callback:
                await progress_callback(slide_no, total_slides, None)
            return None


async def generate_slide_audio(slides: list, lesson_id: str, progress_callback=None) -> list:
    """
    Generates narration audio for all scenes/slides concurrently.
    Saves WAV files to disk and uploads to Supabase Cloud Storage.
    Returns a list of audio URLs strictly ordered matching the slides list.
    """
    MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.abspath(os.path.join(MAIN_DIR, "..", "..", "..", ".."))
    lesson_dir = os.path.join(PROJECT_ROOT, "uploads", "visual_lessons", lesson_id)
    try:
        os.makedirs(lesson_dir, exist_ok=True)
        test_file = os.path.join(lesson_dir, ".write_test")
        with open(test_file, "w") as f:
            f.write("1")
        os.remove(test_file)
    except Exception:
        lesson_dir = os.path.join("/tmp", "uploads", "visual_lessons", lesson_id)
        os.makedirs(lesson_dir, exist_ok=True)
    
    api_key = os.getenv("SARVAM_API_KEY", "")
    total_slides = len(slides)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [
            _generate_single_slide_audio(
                slide=slide,
                slide_idx=idx,
                total_slides=total_slides,
                lesson_dir=lesson_dir,
                lesson_id=lesson_id,
                api_key=api_key,
                client=client,
                progress_callback=progress_callback
            )
            for idx, slide in enumerate(slides, 1)
        ]
        # asyncio.gather strictly preserves input list order for returned results
        audio_urls = await asyncio.gather(*tasks)
        
    return list(audio_urls)

