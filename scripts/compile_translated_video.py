import os
import json
import subprocess
import uuid
import ffmpeg
from commands import Video, KiB, MiB
from video_utils import get_new_video_info


def crop_thumbnail_to_video_ratio(thumbnail_path, video_width, video_height):
    """Crop a landscape thumbnail to match a vertical video's aspect ratio (center crop)."""
    try:
        probe = ffmpeg.probe(thumbnail_path, select_streams='v:0')
        thumb_w = int(probe['streams'][0]['width'])
        thumb_h = int(probe['streams'][0]['height'])

        target_ratio = video_width / video_height
        new_w = int(thumb_h * target_ratio)

        if new_w >= thumb_w:
            return thumbnail_path

        cropped_path = thumbnail_path.replace('.jpg', '_cropped.jpg')
        x_offset = (thumb_w - new_w) // 2

        result = subprocess.run([
            'ffmpeg', '-i', thumbnail_path,
            '-vf', f'crop={new_w}:{thumb_h}:{x_offset}:0',
            '-y', cropped_path
        ], capture_output=True)

        if result.returncode == 0:
            print(f"Cropped thumbnail {thumb_w}x{thumb_h} -> {new_w}x{thumb_h} for vertical video")
            return cropped_path
        else:
            print(f"Thumbnail crop failed, using original")
            return thumbnail_path
    except Exception as e:
        print(f"Thumbnail crop error: {e}")
        return thumbnail_path


def run_ffmpeg_with_fallback(build_command_fn):
    """
    Try QSV hardware encoding first, fall back to libx264 software encoding.

    build_command_fn: callable(encoder, vf_template, hwaccel_args) -> list[str]
        - encoder: 'h264_qsv' or 'libx264'
        - vf_template: format string for -vf with {w} and {h} placeholders
        - hwaccel_args: list of args to prepend (hwaccel flags), empty for software
        - extra_encoder_args: list of extra encoder args (e.g. ['-preset', 'medium'])
    Returns: (subprocess.CompletedProcess, used_fallback: bool)
    """
    from telegram_bot import get_intel_gpu_render_device

    intel_device = get_intel_gpu_render_device()

    # Try QSV first
    if intel_device:
        hwaccel_args = ['-hwaccel', 'qsv', '-qsv_device', intel_device]
        env = os.environ.copy()
        env['LIBVA_DRIVER_NAME'] = 'iHD'
        command = build_command_fn(
            encoder='h264_qsv',
            vf_template='format=nv12,scale={w}:{h}',
            hwaccel_args=hwaccel_args,
            extra_encoder_args=[]
        )
        print(f"Trying QSV encoding: {' '.join(command)}")
        result = subprocess.run(command, env=env)
        if result.returncode == 0:
            return result, False

        print(f"QSV encoding failed (return code {result.returncode}), falling back to software encoding")

    # Software fallback
    command = build_command_fn(
        encoder='libx264',
        vf_template='scale={w}:{h}',
        hwaccel_args=[],
        extra_encoder_args=['-preset', 'medium']
    )
    print(f"Using software encoding: {' '.join(command)}")
    result = subprocess.run(command)
    return result, True

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
    from telegram_bot import run_vot_cli, format_subs, send_message, TRANSLATION_LANG, SUBS_LANG, Str, MESSAGES

    audio_name = "audio.mp3"
    subs_name = "subtitles.json"
    audio_full_name = os.path.join(temp_audio_dir, audio_name)
    subs_full_name = os.path.join(temp_subs_dir, subs_name)

    # Download translated audio
    result = run_vot_cli(temp_audio_dir, audio_name, video_link, TRANSLATION_LANG, False)

    if "Нейросети не смогли определить речь на видео" in result.stdout.decode('utf-8'):
        await send_message(chat_id, f"{title} {MESSAGES.no_speech_detected}")
        return None, None, None, False, "no_speech"

    if "is unknown service" in result.stderr.decode('utf-8'):
        await send_message(chat_id, f"{title} {MESSAGES.unsupported_site}")
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
    Uses QSV hardware encoding with automatic software fallback.
    """
    from telegram_bot import (
        get_video_codec,
        map_lang, TRANSLATION_LANG, SUBS_LANG, DEBUG, send_message
    )

    video_bitrate, audio_bitrate, width, height = get_new_video_info(video_path)

    video_input_codec = get_video_codec(video_path)

    # Determine subtitle inputs
    has_translated_subs = translated_subs_path and os.path.exists(f"{translated_subs_path}.srt")
    has_original_subs = original_subs_path and os.path.exists(original_subs_path)

    # Metadata
    try:
        subs_real_lang_mapped = map_lang(subs_real_lang)
    except (KeyError, TypeError):
        subs_real_lang_mapped = map_lang(SUBS_LANG)

    def build_command(encoder, vf_template, hwaccel_args, extra_encoder_args):
        command = ["ffmpeg"]

        # For software decoding of AV1/VP9, add decoder flags (no hwaccel for decoding)
        if video_input_codec == "av1":
            command += ["-c:v", "libdav1d"]
            print(f"Using software decoding for AV1 input")
        elif video_input_codec == "vp9":
            command += ["-c:v", "libvpx-vp9"]
            print(f"Using software decoding for VP9 input")

        # Add hwaccel args (QSV flags for hw, empty for software)
        command += hwaccel_args

        # Input files
        command += [
            '-i', video_path,       # 0: Original video (with video + original audio)
            '-i', mixed_audio_path, # 1: Pre-mixed audio (30% original + 100% translated)
        ]

        # Add subtitle tracks if available
        subtitle_input_index = 2
        translated_subs_index = None
        original_subs_index = None

        if has_translated_subs:
            command += ['-i', f"{translated_subs_path}.srt"]
            translated_subs_index = subtitle_input_index
            subtitle_input_index += 1

        if has_original_subs:
            command += ['-i', original_subs_path]
            original_subs_index = subtitle_input_index
            subtitle_input_index += 1

        # Video encoding
        vf = vf_template.format(w=width, h=height)
        command += [
            '-vf', vf,
            '-c:v', encoder,
            '-b:v', str(int(video_bitrate)),
        ]
        command += extra_encoder_args

        # Audio codec
        command += ['-c:a', 'aac', '-b:a', str(int(audio_bitrate))]

        # Stream mapping
        command += [
            '-map', '0:v',
            '-map', '1:a',
            '-map', '0:a',
        ]

        # Map subtitle streams
        if has_translated_subs:
            command += [
                '-map', f'{translated_subs_index}:s',
                '-c:s:0', 'mov_text'
            ]

        if has_original_subs:
            subs_stream_num = 1 if has_translated_subs else 0
            command += [
                '-map', f'{original_subs_index}:s',
                f'-c:s:{subs_stream_num}', 'mov_text'
            ]

        # Set default streams
        command += [
            '-disposition:v:0', 'default',
            '-disposition:a:0', 'default',
            '-disposition:a:1', '0',
        ]

        if has_translated_subs:
            command += ['-disposition:s:0', 'default']
        if has_original_subs:
            subs_disposition_index = 1 if has_translated_subs else 0
            command += [f'-disposition:s:{subs_disposition_index}', '0']

        # Metadata
        command += [
            '-metadata:s:a:0', f'language={map_lang(TRANSLATION_LANG)}',
            '-metadata:s:a:1', f'language={subs_real_lang_mapped}',
        ]

        subtitle_stream_counter = 0
        if has_translated_subs:
            command += [
                f'-metadata:s:s:{subtitle_stream_counter}', f'language={map_lang(TRANSLATION_LANG)}',
                f'-metadata:s:s:{subtitle_stream_counter}', 'title=Translated',
            ]
            subtitle_stream_counter += 1

        if has_original_subs:
            original_lang_code = original_subs_lang if original_subs_lang else 'und'
            lang_names = {
                'en': 'English', 'ru': 'Russian', 'es': 'Spanish',
                'fr': 'French', 'de': 'German', 'ja': 'Japanese',
                'zh': 'Chinese', 'ko': 'Korean', 'pt': 'Portuguese',
                'it': 'Italian', 'ar': 'Arabic', 'hi': 'Hindi',
            }
            lang_display = lang_names.get(original_subs_lang, original_subs_lang.upper() if original_subs_lang else 'Unknown')
            command += [
                f'-metadata:s:s:{subtitle_stream_counter}', f'language={original_lang_code}',
                f'-metadata:s:s:{subtitle_stream_counter}', f'title=Original ({lang_display})',
            ]

        command += ['-y', output_path]
        return command

    # Debug output before running
    if DEBUG:
        preview_cmd = build_command('h264_qsv', 'format=nv12,scale={w}:{h}', [], [])
        cmd_str = " ".join(f'"{s}"' if "[" in s else s for s in preview_cmd)
        await send_message(chat_id, title + " " + cmd_str)

    print("Running ffmpeg with QSV + fallback...")
    result, used_fallback = run_ffmpeg_with_fallback(build_command)

    if used_fallback:
        print("Used software encoding fallback (libx264)")
        await send_message(chat_id, f"{title} {MESSAGES.hw_accel_unavailable}")

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

    STATUS_MANAGER.add(chat_id, await send_message(chat_id, f"{title} {MESSAGES.video_downloaded_translating}"))

    # Step 3: Download translations (audio and subtitles)
    # This now properly handles vot-cli calls - fixed the duplicate call issue!
    audio_full_name, subs_full_name, subs_real_lang, success, error_msg = await download_translations(
        video_link, temp_audio_dir, temp_subs_dir, chat_id, title
    )

    if not success:
        # Error message already sent in download_translations
        is_known_error = error_msg in ["no_speech", "unknown_service"]
        return None, None, None, None, is_known_error, None

    STATUS_MANAGER.add(chat_id, await send_message(chat_id, f"{title} {MESSAGES.translation_done_mixing}"))

    # Step 4: Mix audio separately (30% original + 100% translated)
    mixed_audio_path = os.path.join(temp_audio_dir, "mixed_audio.mp3")
    mixed_audio_result = mix_audio_files(
        video_full_name,
        audio_full_name,
        mixed_audio_path,
        original_volume=0.3
    )

    if not mixed_audio_result:
        await send_message(chat_id, f"{title} {MESSAGES.audio_mixing_error}")
        return None, None, None, None, False, None

    STATUS_MANAGER.add(chat_id, await send_message(chat_id, f"{title} {MESSAGES.assembling_video}"))

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
        # Crop thumbnail to match video aspect ratio for vertical videos
        if thumbnail and height > width:
            thumbnail = crop_thumbnail_to_video_ratio(thumbnail, width, height)
        return final_video, height, width, temp_dir, False, thumbnail
    else:
        return None, None, None, None, False, thumbnail
