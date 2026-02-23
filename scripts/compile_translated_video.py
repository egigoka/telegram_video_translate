import os
import json
import subprocess
import uuid
import ffmpeg
from commands import Video, KiB, MiB
from video_utils import get_new_video_info

MAX_VIDEO_SIZE = 2 * 1024 * 1024 * 1024
MIN_AUDIO_BITRATE = 32 * KiB
MAX_AUDIO_BITRATE = 256 * KiB
ADDITIONAL_VIDEO_BITRATE_MULTIPLIER = 0.95


def setup_temp_folders():
    """
    Create temporary folder structure for video processing.
    Returns: (temp_dir, temp_video_dir, temp_audio_dir, temp_subs_dir, temp_thumbnail_dir)
    """
    from telegram_bot import Dir

    temp_dir = os.path.join('.', str(uuid.uuid4()))
    temp_video_dir = os.path.join(temp_dir, 'video')
    temp_audio_dir = os.path.join(temp_dir, 'audio')
    temp_subs_dir = os.path.join(temp_dir, 'subtitles')
    temp_thumbnail_dir = os.path.join(temp_dir, 'thumbnail')

    if os.path.exists(temp_dir):
        Dir.delete(temp_dir)

    os.makedirs(temp_video_dir)
    os.makedirs(temp_audio_dir)
    os.makedirs(temp_subs_dir)
    os.makedirs(temp_thumbnail_dir)

    return temp_dir, temp_video_dir, temp_audio_dir, temp_subs_dir, temp_thumbnail_dir


async def download_translations(video_link, temp_audio_dir, temp_subs_dir, chat_id, title):
    """
    Download translated audio and subtitles using vot-cli.
    Returns: (audio_full_name, subs_full_name, subs_real_lang, success, error_message)
    """
    from telegram_bot import run_vot_cli, format_subs, send_message, TRANSLATION_LANG, SUBS_LANG, Str

    audio_name = "audio.mp3"
    subs_name = "subtitles.json"
    audio_full_name = os.path.join(temp_audio_dir, audio_name)
    subs_full_name = os.path.join(temp_subs_dir, subs_name)

    # Download translated audio
    result = run_vot_cli(temp_audio_dir, audio_name, video_link, TRANSLATION_LANG, False)

    if "Нейросети не смогли определить речь на видео" in result.stdout.decode('utf-8'):
        await send_message(chat_id, title + " Не смогли определить речь на видео :(")
        return None, None, None, False, "no_speech"

    if "is unknown service" in result.stderr.decode('utf-8'):
        await send_message(chat_id, title + " Не умею переводить видео с этого сайта :(")
        return None, None, None, False, "unknown_service"

    if result.returncode != 0:
        await send_message(chat_id, title + " " + result.stderr.decode('utf-8'))
        return None, None, None, False, "audio_error"

    print(result.stdout.decode('utf-8'))
    print(result.stderr.decode('utf-8'))

    # Download subtitles - FIXED: removed duplicate vot-cli call
    result = run_vot_cli(temp_subs_dir, subs_name, video_link, SUBS_LANG, True)

    subs_real_lang = None
    try:
        json_subs_langs = Str.substring(result.stdout.decode('utf-8'), "): \"", "\n]\"")
        json_subs_langs = json.loads(json_subs_langs + "]")
        subs_lang_json = json_subs_langs[0]["language"]
        if subs_lang_json == SUBS_LANG:
            subs_real_lang = SUBS_LANG
        else:
            subs_real_lang = subs_lang_json
            # Re-download with correct language
            result = run_vot_cli(temp_subs_dir, subs_name, video_link, subs_lang_json, True)
    except (KeyError, IndexError, json.JSONDecodeError):
        pass

    if result.returncode != 0:
        await send_message(chat_id, title + " " + result.stderr.decode('utf-8'))
        return None, None, None, False, "subs_error"

    print(result.stdout.decode('utf-8'))
    print(result.stderr.decode('utf-8'))

    if os.path.exists(subs_full_name):
        format_subs(subs_full_name)

    return audio_full_name, subs_full_name, subs_real_lang, True, None


def mix_audio_files(video_path, translated_audio_path, output_path, original_volume=0.3):
    """
    Mix original video audio with translated audio separately.
    original_volume: Volume multiplier for original audio (default 0.3 = 30%)
    Returns: path to mixed audio file
    """
    print("Attempting to mix audio files...")
    try:
        # Get audio bitrate from original video
        print("Getting new video info for audio mixing...")
        _, audio_bitrate, _, _ = get_new_video_info(video_path)
        print(f"Audio bitrate from get_new_video_info: {audio_bitrate}")

        # Mix audio: 30% original + 100% translated
        command = [
            'ffmpeg',
            '-i', video_path,        # Original video (for audio)
            '-i', translated_audio_path,  # Translated audio
            '-filter_complex',
            f"[0:a]volume={original_volume}[original];[original][1:a]amix=inputs=2:duration=longest",
            '-b:a', str(int(audio_bitrate)),
            '-y',
            output_path
        ]
        print(f"ffmpeg command for audio mixing: {' '.join(command)}")

        result = subprocess.run(command, capture_output=True, text=True)

        if result.returncode == 0:
            print("Audio mixing successful.")
            return output_path
        else:
            print(f"Audio mixing error. Return code: {result.returncode}")
            print(f"ffmpeg stdout: {result.stdout}")
            print(f"ffmpeg stderr: {result.stderr}")
            return None
    except Exception as e:
        import traceback
        print(f"An exception occurred in mix_audio_files: {e}")
        print(traceback.format_exc())
        return None


async def assemble_final_video(video_path, mixed_audio_path, translated_subs_path, original_subs_path,
                                original_subs_lang, output_path, subs_real_lang, chat_id, title):
    """
    Assemble final video with all streams: video, mixed audio, original audio, and subtitles.
    IMPORTANT: This preserves all streams to avoid losing video/audio/subs!

    Note: Original audio comes from the video_path input (0:a), not a separate file.
    Now includes BOTH translated and original subtitle tracks!

    Args:
        original_subs_lang: Language code of original subtitles (e.g., 'en', 'ru', 'es')
    """
    from telegram_bot import (
        get_amd_gpu_render_device, get_video_codec,
        map_lang, TRANSLATION_LANG, SUBS_LANG, DEBUG, send_message
    )

    video_bitrate, audio_bitrate, width, height = get_new_video_info(video_path)

    amd_device = get_amd_gpu_render_device()
    if not amd_device:
        amd_device = "/dev/dri/renderD128"

    video_input_codec = get_video_codec(video_path)

    command = ["ffmpeg"]

    # Hardware acceleration setup based on codec
    if video_input_codec == "av1":
        # AV1: use software decoder (libdav1d) because hardware decoding has issues
        # But initialize VAAPI device for hardware encoding via hwupload
        command += [
            "-init_hw_device", f"vaapi=va:{amd_device}",
            "-filter_hw_device", "va",
            "-c:v", "libdav1d"
        ]
        print(f"Using software decoding for AV1 input with hardware encoding")
    elif video_input_codec == "vp9":
        # VP9: use software decoder (libvpx-vp9) because hardware decoding has issues
        # But initialize VAAPI device for hardware encoding via hwupload
        command += [
            "-init_hw_device", f"vaapi=va:{amd_device}",
            "-filter_hw_device", "va",
            "-c:v", "libvpx-vp9"
        ]
        print(f"Using software decoding for VP9 input with hardware encoding")
    elif amd_device: # For other codecs, use hardware acceleration for decoding
        command += [
            '-hwaccel', 'vaapi',
            '-vaapi_device', amd_device
        ]
        print(f"Using hardware acceleration for {video_input_codec} input")

    # Input files
    command += [
        '-i', video_path,       # 0: Original video (with video + original audio)
        '-i', mixed_audio_path, # 1: Pre-mixed audio (30% original + 100% translated)
    ]

    # Add subtitle tracks if available
    has_translated_subs = translated_subs_path and os.path.exists(f"{translated_subs_path}.srt")
    has_original_subs = original_subs_path and os.path.exists(original_subs_path)

    subtitle_input_index = 2  # Track which input index we're at
    translated_subs_index = None
    original_subs_index = None

    if has_translated_subs:
        command += ['-i', f"{translated_subs_path}.srt"]
        translated_subs_index = subtitle_input_index
        subtitle_input_index += 1
        print(f"Adding translated subtitles at index {translated_subs_index}")

    if has_original_subs:
        command += ['-i', original_subs_path]
        original_subs_index = subtitle_input_index
        subtitle_input_index += 1
        print(f"Adding original subtitles at index {original_subs_index}")

    # Video encoding - always use hardware encoding with VAAPI
    command += [
        '-vf', f'format=nv12,hwupload,scale_vaapi={width}:{height}',
        '-c:v', 'h264_vaapi',
        '-b:v', str(int(video_bitrate)),
        # Note: -crf, -preset, and -profile:v are not valid for h264_vaapi
    ]
    print(f"Using hardware encoding (h264_vaapi) for output")

    # Audio codec
    command += ['-c:a', 'aac', '-b:a', str(int(audio_bitrate))]

    # Stream mapping - IMPORTANT: Map all streams to preserve them!
    command += [
        '-map', '0:v',  # OUTPUT STREAM 0: Video from original
        '-map', '1:a',  # OUTPUT STREAM 1: Mixed audio (translated + 30% original)
        '-map', '0:a',  # OUTPUT STREAM 2: Original audio track
    ]

    # Map subtitle streams
    if has_translated_subs:
        command += [
            '-map', f'{translated_subs_index}:s',  # OUTPUT STREAM 3: Translated subtitles
            '-c:s:0', 'mov_text'                   # Subtitle codec for first subtitle track
        ]

    if has_original_subs:
        subs_stream_num = 1 if has_translated_subs else 0  # Second or first subtitle stream
        command += [
            '-map', f'{original_subs_index}:s',           # OUTPUT STREAM 4 (or 3): Original subtitles
            f'-c:s:{subs_stream_num}', 'mov_text'         # Subtitle codec for this track
        ]

    # Set default streams: mixed audio and translated subtitles
    command += [
        '-disposition:v:0', 'default',    # Video stream is default (only one anyway)
        '-disposition:a:0', 'default',    # Stream 1: Mixed audio is default
        '-disposition:a:1', '0',          # Stream 2: Original audio is NOT default
    ]

    # Set subtitle defaults
    if has_translated_subs:
        command += ['-disposition:s:0', 'default']  # Translated subtitles are default
    if has_original_subs:
        subs_disposition_index = 1 if has_translated_subs else 0
        command += [f'-disposition:s:{subs_disposition_index}', '0']  # Original subs NOT default

    # Metadata
    try:
        subs_real_lang_mapped = map_lang(subs_real_lang)
    except (KeyError, TypeError):
        subs_real_lang_mapped = map_lang(SUBS_LANG)

    command += [
        '-metadata:s:a:0', f'language={map_lang(TRANSLATION_LANG)}',  # Mixed audio track
        '-metadata:s:a:1', f'language={subs_real_lang_mapped}',       # Original audio track
    ]

    # Add subtitle metadata
    subtitle_stream_counter = 0
    if has_translated_subs:
        command += [
            f'-metadata:s:s:{subtitle_stream_counter}', f'language={map_lang(TRANSLATION_LANG)}',  # FIXED: Translated subs in Russian
            f'-metadata:s:s:{subtitle_stream_counter}', 'title=Translated',
        ]
        subtitle_stream_counter += 1

    if has_original_subs:
        # Use detected language or default to 'und' (undefined) if not detected
        original_lang_code = original_subs_lang if original_subs_lang else 'und'
        # Create a human-readable title
        lang_names = {
            'en': 'English',
            'ru': 'Russian',
            'es': 'Spanish',
            'fr': 'French',
            'de': 'German',
            'ja': 'Japanese',
            'zh': 'Chinese',
            'ko': 'Korean',
            'pt': 'Portuguese',
            'it': 'Italian',
            'ar': 'Arabic',
            'hi': 'Hindi',
        }
        lang_display = lang_names.get(original_subs_lang, original_subs_lang.upper() if original_subs_lang else 'Unknown')

        command += [
            f'-metadata:s:s:{subtitle_stream_counter}', f'language={original_lang_code}',
            f'-metadata:s:s:{subtitle_stream_counter}', f'title=Original ({lang_display})',
        ]

    command += [
        '-y',
        output_path
    ]

    # Debug output
    if DEBUG:
        print("ffmpeg command:", end="")
        cmd_str = ""
        for sub in command:
            if "[" in sub:
                sub = f"\"{sub}\""
            print(f" {sub}", end="")
            cmd_str += f" {sub}"
        print()

        await send_message(chat_id, title + " " + cmd_str)

    print("Running ffmpeg command (this may take a while)...")
    print(f"Command: {' '.join(command)}")
    result = subprocess.run(command)  # Show output in real-time
    print(f"ffmpeg completed with return code: {result.returncode}")

    if result.returncode == 0:
        return output_path
    else:
        print(f"Video assembly error: ffmpeg returned code {result.returncode}")
        return None


async def compile_translated_video(video_link, chat_id, title):
    """
    Main function to compile translated video.
    Refactored into smaller, manageable functions for better maintainability.
    """
    from telegram_bot import (
        STATUS_MANAGER, send_message, download_video, get_thumbnail, MESSAGES
    )

    # Step 1: Setup temporary folder structure
    temp_dir, temp_video_dir, temp_audio_dir, temp_subs_dir, temp_thumbnail_dir = setup_temp_folders()

    # Step 2: Download original video and subtitles
    video_full_name, original_subs_path, original_subs_lang = await download_video(video_link, temp_video_dir, chat_id)

    if not video_full_name:
        await send_message(chat_id, title + " " + MESSAGES.loading_error)
        return None, None, None, None, False, None

    if original_subs_path:
        print(f"Downloaded original subtitles: {original_subs_path} (language: {original_subs_lang})")

    STATUS_MANAGER.add(chat_id, await send_message(chat_id, title + " Видео качнул, пытаюсь перевести"))

    # Step 3: Download translations (audio and subtitles)
    # This now properly handles vot-cli calls - fixed the duplicate call issue!
    audio_full_name, subs_full_name, subs_real_lang, success, error_msg = await download_translations(
        video_link, temp_audio_dir, temp_subs_dir, chat_id, title
    )

    if not success:
        # Error message already sent in download_translations
        is_known_error = error_msg in ["no_speech", "unknown_service"]
        return None, None, None, None, is_known_error, None

    STATUS_MANAGER.add(chat_id, await send_message(chat_id, f"{title} Перевел, микширую аудио"))

    # Step 4: Mix audio separately (30% original + 100% translated)
    mixed_audio_path = os.path.join(temp_audio_dir, "mixed_audio.mp3")
    mixed_audio_result = mix_audio_files(
        video_full_name,
        audio_full_name,
        mixed_audio_path,
        original_volume=0.3
    )

    if not mixed_audio_result:
        await send_message(chat_id, title + " Ошибка при микшировании аудио")
        return None, None, None, None, False, None

    STATUS_MANAGER.add(chat_id, await send_message(chat_id, f"{title} Собираю финальное видео"))

    # Step 5: Assemble final video with all streams
    final_video_full_name = f"{os.path.splitext(video_full_name)[0]}_final.mp4"
    final_video = await assemble_final_video(
        video_full_name,
        mixed_audio_path,
        subs_full_name,           # Translated subtitles
        original_subs_path,       # Original subtitles from yt-dlp
        original_subs_lang,       # Language code of original subtitles
        final_video_full_name,
        subs_real_lang,
        chat_id,
        title
    )

    # Step 6: Get thumbnail
    thumbnail = get_thumbnail(video_link, temp_thumbnail_dir)
    print("--------------------------------------------")
    print(f"Thumbnail: {thumbnail}")
    print("--------------------------------------------")

    if final_video:
        _, _, width, height = get_new_video_info(final_video)
        return final_video, height, width, temp_dir, False, thumbnail
    else:
        return None, None, None, None, False, thumbnail
