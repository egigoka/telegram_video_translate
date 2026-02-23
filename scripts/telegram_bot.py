import telebot
from telebot.async_telebot import AsyncTeleBot
import os
import traceback
import inspect
import subprocess
import time
import uuid
import json
import ffmpeg
import asyncio
import openai
import requests
import argparse
from json import JSONDecodeError
from commands import Video, File, KiB, MiB, Bench, Dir, Str, OS, Print, Console, Int
from secrets_ import TELEGRAM_TOKEN, ADMIN_CHAT_ID, APP_ID, APP_API_HASH, OPENAI_API_KEY
from messages import Messages
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeFilename
import logging

__print = print

def print(*args, **kwargs):
    kwargs["flush"] = True
    __print(*args, **kwargs)

logging.basicConfig(level=logging.WARNING)

# Silence noisy third-party loggers
logging.getLogger('telethon').setLevel(logging.ERROR)
logging.getLogger('asyncio').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('openai').setLevel(logging.WARNING)

# Status Message Manager Singleton
class StatusMessageManager:
    """Singleton class to manage status messages for the Telegram bot per chat"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._messages_by_chat = {}
        return cls._instance

    def add(self, chat_id, messages):
        """Add message(s) to the status message list for a specific chat"""
        if chat_id not in self._messages_by_chat:
            self._messages_by_chat[chat_id] = []

        if isinstance(messages, list):
            self._messages_by_chat[chat_id].extend(messages)
        else:
            self._messages_by_chat[chat_id].append(messages)
        return messages

    async def clear(self, chat_id):
        """Clear all status messages for a specific chat by deleting them from Telegram"""
        if chat_id not in self._messages_by_chat:
            return

        if not DEBUG:
            for messages in self._messages_by_chat[chat_id]:
                await delete_messages(messages)

        self._messages_by_chat[chat_id].clear()
        del self._messages_by_chat[chat_id]

    def get_all(self, chat_id):
        """Get all current status messages for a specific chat"""
        return self._messages_by_chat.get(chat_id, []).copy()

    def reset(self, chat_id):
        """Reset the message list for a specific chat without deleting from Telegram"""
        if chat_id in self._messages_by_chat:
            self._messages_by_chat[chat_id].clear()
            del self._messages_by_chat[chat_id]

# Initialize the Telegram bot with your token
BOT = AsyncTeleBot(TELEGRAM_TOKEN)
TELETHON_CLIENT = TelegramClient('session', APP_ID, APP_API_HASH)
STATUS_MANAGER = StatusMessageManager()
MAX_VIDEO_SIZE = 2 * 1024 * 1024 * 1024
MIN_AUDIO_BITRATE = 32 * KiB
MAX_AUDIO_BITRATE = 256 * KiB
LANG = 'ru'
MESSAGES = Messages(LANG)
TRANSLATION_LANG = 'ru'
SUBS_LANG = 'en'
ADDITIONAL_VIDEO_BITRATE_MULTIPLIER = 0.95
DEBUG = True

OPENAI_CLIENT = openai.OpenAI(api_key=OPENAI_API_KEY)

async def telegram_send_message(telegram_api_object, chat_id, text,
                 disable_web_page_preview=None, reply_to_message_id=None, reply_markup=None,
                 parse_mode=None, disable_notification=None):
    if len(text) in Int.from_to(1, 4095):
        texts = [text]
    elif len(text) > 4096:
        texts = Str.split_every(text, 4096)
    else:
        texts = ["<empty message>"]
    output = []
    for text in texts:
        for i in range(10):
            try:
                output.append(await telegram_api_object.send_message(chat_id=chat_id, text=text,
                                                               disable_web_page_preview=disable_web_page_preview,
                                                               reply_to_message_id=reply_to_message_id,
                                                               reply_markup=reply_markup, parse_mode=parse_mode,
                                                               disable_notification=disable_notification,
                                                               timeout=300))
                break
            except (requests.exceptions.ReadTimeout, 
                    telebot.asyncio_helper.RequestTimeout):
                pass
    if len(output) == 0:
        return []
    elif len(output) == 1:
        return output
    else:
        return output

async def async_send_video(
    chat_id,
    video,
    supports_streaming,
    height,
    width,
    caption,
    thumbnail
):
    # Ensure client is connected before sending
    if not TELETHON_CLIENT.is_connected():
        await TELETHON_CLIENT.start()

    video_attributes = DocumentAttributeVideo(
        w=width,
        h=height,
        supports_streaming=supports_streaming,
        duration=0
    )
    file_attributes = DocumentAttributeFilename(file_name=video)
    attributes = [video_attributes, file_attributes]

    messages = []
    try:
        message = await TELETHON_CLIENT.send_file(
            entity=chat_id,
            file=video,
            attributes=attributes,
            progress_callback=progress,
            supports_streaming=supports_streaming,
            caption=caption,
            thumb=thumbnail
        )
        messages.append(message)

        if chat_id != ADMIN_CHAT_ID:
            admin_message = await TELETHON_CLIENT.send_file(
                entity=ADMIN_CHAT_ID,
                file=video,
                attributes=attributes,
                progress_callback=progress,
                supports_streaming=supports_streaming,
                caption=caption,
                thumb=thumbnail
            )
            messages.append(admin_message)

    except Exception as e:
        print(f"Error sending video: {e}")
        raise

    await clear_status_messages(chat_id)

    return messages


def translate_video_title(title, target_language):
    try:
        # model = 'gpt-4o-mini'
        model = 'gpt-5-mini'
        messages = [
                {"role": "system", "content": "You are a translator. Do not add quotes."},
                {"role": "user", "content": f"Translate this video title to {target_language}: ```{title}```"}
        ]
        response = get_messages_gpt(OPENAI_CLIENT, model, messages, 500)
        print(response)
        translation = response.choices[0].message.content.strip()
        return translation
    except Exception as e:
        print(f"Error translating text: {e}")
        return None


def get_messages_gpt(openai_client, model, messages, max_tokens, max_retries=10, retry=1):
    try:
        response = openai_client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens
        )
    except openai.RateLimitError as e:
        if "quota" in str(e).lower():
            raise OpenAiQuotaError from e
        if retry > max_retries:
            raise
        response_json = e.response.json()
        if response_json['error']['type'] == 'tokens':
            tokens_count_new = Str.substring(response_json['error']['message'], ' on tokens per min (TPM): Limit ')
            tokens_count_new = Str.get_integers(tokens_count_new)[0]
            requested_tokens = Str.substring(response_json['error']['message'], 'Requested ')
            requested_tokens = Str.get_integers(requested_tokens)[0]
            raise TooManyTokensError(tokens_count_new=tokens_count_new, requested_tokens=requested_tokens)
        time.sleep(pow(2, retry))
        response = get_messages_gpt(openai_client=openai_client,
                                    model=model,
                                    messages=messages,
                                    max_tokens=max_tokens,
                                    max_retries=max_retries,
                                    retry=retry + 1)
    return response


class TooManyTokensError(Exception):
    def __init__(self, tokens_count_new, requested_tokens):
        self.tokens_count_new = tokens_count_new
        self.requested_tokens = requested_tokens


class OpenAiQuotaError(Exception):
    pass


def get_thumbnail(url, folder):
    command = ["yt-dlp",
               "--remote-components", "ejs:github",
               "-o", f"{folder}/_thumbnail.%(ext)s",
               "--write-thumbnail",
               "--skip-download",
               "--convert-thumbnails", "jpg",
               url]
    subprocess.run(command)
    thumbs = os.listdir(folder)
    sorted_thumbs = sorted(thumbs)
    try:
        return os.path.join(folder, sorted_thumbs[-1])
    except IndexError:
        return None


def upload_file(telethon_client, path):
    async def get_file_object():
        return await telethon_client.upload_file(path)

    with telethon_client:
        return telethon_client.loop.run_until_complete(get_file_object())


def progress(bytes_sent, total):
    print(" " * (Console.width() - 1), end="\r")
    print(bytes_sent, "/", total, end="\r", flush=True)


async def send_video(
    chat_id,
    video,
    supports_streaming,
    height,
    width,
    caption,
    thumbnail
):
    video_attributes = DocumentAttributeVideo(
        w=width,
        h=height,
        supports_streaming=supports_streaming,
        duration=0
    )
    file_attributes = DocumentAttributeFilename(file_name=video)
    attributes = [video_attributes, file_attributes]

    async def send_file_to_chat(target_chat_id):
        return await TELETHON_CLIENT.send_file(
            entity=target_chat_id,
            file=video,
            attributes=attributes,
            progress_callback=progress,
            supports_streaming=supports_streaming,
            caption=caption,
            thumb=thumbnail
        )

    messages = []
    # Remove async with context manager
    try:
        # Ensure client is connected first
        if not TELETHON_CLIENT.is_connected():
            await TELETHON_CLIENT.connect()

        messages.append(await send_file_to_chat(chat_id))

        if chat_id != ADMIN_CHAT_ID:
            messages.append(await send_file_to_chat(ADMIN_CHAT_ID))

    finally:
        # Optionally disconnect if needed
        if TELETHON_CLIENT.is_connected():
            await TELETHON_CLIENT.disconnect()

    await clear_status_messages(chat_id)
    return messages


async def send_message(chat_id, text):
    print(chat_id, chat_id == ADMIN_CHAT_ID, text)
    messages = await telegram_send_message(BOT, chat_id=chat_id, text=text)
    if chat_id != ADMIN_CHAT_ID:
        messages += await telegram_send_message(BOT,
                                           chat_id=ADMIN_CHAT_ID,
                                           text=text)
    await clear_status_messages(chat_id)
    return messages


async def telegram_delete_message(telegram_api: telebot.TeleBot, chat_id, message_id):
    retries = 50
    for i in range(retries):
        try:
            result = await telegram_api.delete_message(chat_id, message_id)
            return result
        except telebot.asyncio_helper.RequestTimeout as e:
            print(f"Request timeout while deleting message: {e}")
            await asyncio.sleep(60)
        except Exception as e:
            print(f"Unexpected error: {e}")
            return None
    return None


async def delete_messages(messages):
    for message in messages:
        await telegram_delete_message(BOT, message.chat.id, message.message_id)


def get_video_title(url):
    result = subprocess.run(
        ["yt-dlp", "--remote-components", "ejs:github", "--get-title", url],
        text=True,
        capture_output=True
    )
    return result.stdout.strip()


async def download_video(video_link, temp_video_dir, chat_id):
    output_path = os.path.join(temp_video_dir, 'video.mp4')
    output_basename = os.path.join(temp_video_dir, 'video')
    attempts = 0
    while attempts < 10:
        try:
            yt_dlp_command = [
                "yt-dlp",
                "--remote-components", "ejs:github",
                "-f", "bestvideo[height<=1080]+bestaudio",
                "--merge-output-format", "mp4",
                "--write-subs",              # Download subtitles
                "--write-auto-subs",         # Download auto-generated subs if manual not available
                "--convert-subs", "srt",     # Convert to SRT format
                "-o", output_path,
                video_link
            ]

            # Check for cookie file
            cookie_file_name = f"cookies-{chat_id}.txt"
            cookie_file_path = os.path.join(os.path.dirname(__file__), cookie_file_name)
            if os.path.exists(cookie_file_path):
                yt_dlp_command.insert(1, "--cookies") # Insert after "yt-dlp"
                yt_dlp_command.insert(2, cookie_file_path) # Insert path

            subprocess.check_output(yt_dlp_command, )
            for i in range(10):
                print()
            print(yt_dlp_command)
            for i in range(10):
                print()
            subprocess.check_output(["ffprobe", output_path])
            for i in range(10):
                print()
            break
        except subprocess.CalledProcessError:
            attempts += 1
            STATUS_MANAGER.add(chat_id, await send_message(chat_id,
                                                f'{MESSAGES.loading_error} {MESSAGES.retrying}{attempts}'))
            time.sleep(5)
    else:
        STATUS_MANAGER.add(chat_id, await send_message(chat_id, f'{MESSAGES.fail} {MESSAGES.loading_error}'))
        return None, None, None

    # Find downloaded subtitles (any language)
    original_subs_path = None
    original_subs_lang = None

    # List all .srt files in the directory
    import glob
    subs_files = glob.glob(f"{output_basename}.*.srt")

    if subs_files:
        # Sort to get consistent behavior (prefer manual over auto-generated)
        subs_files.sort()
        original_subs_path = subs_files[0]

        # Extract language code from filename (e.g., video.en.srt -> en)
        filename = os.path.basename(original_subs_path)
        parts = filename.split('.')
        if len(parts) >= 3:
            original_subs_lang = parts[-2]  # Language code is before .srt

        print(f"Found original subtitles: {original_subs_path} (language: {original_subs_lang})")

    return output_path, original_subs_path, original_subs_lang


def get_video_codec(file_path):
    # Run ffprobe to get codec information
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=codec_name", "-of", "json", file_path
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # Parse the JSON output
    info = json.loads(result.stdout)
    codec_name = info['streams'][0]['codec_name'] if 'streams' in info and info['streams'] else None
    return codec_name


def format_subs(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            subs_data = json.load(f)
    except JSONDecodeError:
        pass

    with open(f'{path}.srt', 'w', encoding='utf-8') as f:
        for sub in subs_data['subtitles']:
            if '[ Music]' in sub['text']:
                continue
            start_time = sub['startMs'] // 1000
            end_time = (sub['startMs'] + sub['durationMs']) // 1000
            start_ms = int(sub['startMs'] % 1000 // 10)
            end_ms = int((sub['startMs'] + sub['durationMs']) % 1000 // 10)
            f.write(
                f"0\n{time.strftime('%H:%M:%S', time.gmtime(start_time))}.{start_ms:02d} "
                f"--> {time.strftime('%H:%M:%S', time.gmtime(end_time))}.{end_ms:02d}\n{sub['text']}\n\n"
            )


def map_lang(lang):
    mapping = {
        'en': 'english',
        'ru': 'russian',
        'kk': 'kazakh',
        'ja': 'japanese',
    }

    return mapping[lang]


def run_vot_cli(temp_dir, output_name, video_link, lang, is_subs):
    command = ['vot-cli', '--output', temp_dir, '--reslang', lang,
               "--output-file", output_name, video_link]
    if is_subs:
        command.insert(3, '--subs')
    result = subprocess.run(command,
                            stderr=subprocess.PIPE,
                            stdout=subprocess.PIPE)
    return result


async def clear_status_messages(chat_id):
    """Clear status messages for a chat using the StatusMessageManager"""
    await STATUS_MANAGER.clear(chat_id)

from compile_translated_video import compile_translated_video

def current_function_name():
    return inspect.stack()[1].function


@BOT.message_handler(commands=['start'])
async def send_welcome(message):
    await send_message(message.chat.id,
                 "Привет! Я бот, который переводит видео. Просто отправь мне ссылку на видео, и я переведу его.")


@BOT.message_handler(content_types=['document'])
async def handle_document(message):
    chat_id = message.chat.id
    if message.document.file_name and "cookie" in message.document.file_name.lower():
        # Download the file
        file_info = await BOT.get_file(message.document.file_id)
        downloaded_file = await BOT.download_file(file_info.file_path)

        cookie_file_name = f"cookies-{chat_id}.txt"
        cookie_file_path = os.path.join(os.path.dirname(__file__), cookie_file_name)

        with open(cookie_file_path, 'wb') as new_file:
            new_file.write(downloaded_file)

        await send_message(chat_id, f"Cookie file saved as {cookie_file_name}. It will be used for yt-dlp downloads.")
    else:
        await send_message(chat_id, "Received a document, but it does not appear to be a cookie file. Please send a file with 'cookie' in its name if you want to provide cookies.")


async def compress_video(video_path, chat_id, title):
    from compile_translated_video import run_ffmpeg_with_fallback

    b = Bench(verbose=True)
    b.end(prefix_string=f"start {current_function_name()}")
    ext = File.get_extension(video_path)
    compressed_video_path = video_path.replace(ext, "_compressed.mp4")

    from video_utils import get_new_video_info
    new_video_bitrate, new_audio_bitrate, new_width, new_height = get_new_video_info(video_path)

    for i in range(7):
        b.end(prefix_string=f"iteration {i} {current_function_name()}, "
                            f"new_video_bitrate: {new_video_bitrate}, "
                            f"new_audio_bitrate: {new_audio_bitrate}")

        def build_compress_command(encoder, vf_template, hwaccel_args, extra_encoder_args):
            vf = vf_template.format(w=new_width, h=new_height)
            command = ["ffmpeg"]
            command += hwaccel_args
            command += [
                "-i", video_path,
                "-vf", vf,
                "-c:v", encoder,
            ]
            command += extra_encoder_args
            command += [
                "-b:v", str(int(new_video_bitrate)),
                "-b:a", str(int(new_audio_bitrate)),
                "-c:s", "copy",
                "-y",
                compressed_video_path,
            ]
            return command

        print("ffmpeg command (with QSV + fallback):")
        b.end(prefix_string=f"command {current_function_name()}")

        result, used_fallback = run_ffmpeg_with_fallback(build_compress_command)

        if used_fallback:
            await send_message(chat_id, f"{title} HW ускорение недоступно, использую программный кодек")

        if DEBUG:
            await send_message(chat_id, str(result))

        if not os.path.exists(compressed_video_path):
            return None

        if os.path.getsize(compressed_video_path) > MAX_VIDEO_SIZE:
            STATUS_MANAGER.add(chat_id,
                await send_message(chat_id, f"{title} Сжалось фигово: {int(os.path.getsize(compressed_video_path) / MiB) + 1} мегов. "
                                      f"Пробуем ещё раз"))

            new_audio_bitrate = int(new_audio_bitrate * 0.9)
            new_audio_bitrate = MIN_AUDIO_BITRATE if new_audio_bitrate < MIN_AUDIO_BITRATE else new_audio_bitrate
            new_video_bitrate = int(new_video_bitrate * 0.9)
            continue

        return compressed_video_path, new_width, new_height


def get_intel_gpu_render_device():
    vendor_id = "0x8086"
    return get_gpu_render_device(vendor_id)

def get_amd_gpu_render_device():
    vendor_id = "0x1002"
    return get_gpu_render_device(vendor_id)


def get_gpu_render_device(vendor_id):
    drm_path = "/sys/class/drm"

    for node in os.listdir(drm_path):
        if node.startswith("renderD"):
            node_path = os.path.join(drm_path, node, "device")
            vendor_path = os.path.join(node_path, "vendor")

            try:
                with open(vendor_path, "r") as f:
                    vendor_id_read = f.read().strip()

                # AMD GPUs typically have vendor ID 0x1002
                if vendor_id_read == vendor_id:
                    return f"/dev/dri/{node}"

            except FileNotFoundError:
                continue
    return None


# Handle received links
@BOT.message_handler(func=lambda message: True)
async def handle_message(message):
    global DEBUG
    b = Bench(verbose=True)
    link = message.text
    chat_id = message.chat.id
    message_id = message.message_id

    if chat_id != ADMIN_CHAT_ID:
        await BOT.forward_message(ADMIN_CHAT_ID, chat_id, message_id)

    if link.lower() == "debug":
        DEBUG = not DEBUG
        return

    try:
        title = get_video_title(link)
    except Exception:
        title = ""

    try:
        STATUS_MANAGER.add(chat_id, await send_message(chat_id, title + " Паажалуйста паажите..."))
        b.end(prefix_string=f"start {current_function_name()}")

        video_path, video_height, video_width, temp_dir, known_error, thumbnail = \
            await compile_translated_video(link,
                                     chat_id,
                                     title)

        if not video_path:
            if not known_error:
                await send_message(chat_id, title + " Не перевелось, бля, сорян, мусорян. ")
            return

        b.end(prefix_string=f"completed {current_function_name()}")

        if os.path.exists(video_path):

            if os.path.getsize(video_path) > MAX_VIDEO_SIZE:
                STATUS_MANAGER.add(chat_id,
                    await send_message(chat_id, f"{title} Видос слишком большой - {int(os.path.getsize(video_path) / MiB)} мегов. "
                                          f"Пробую сжать до {int(MAX_VIDEO_SIZE / MiB)}."))
                video_path, video_width, video_height = await compress_video(video_path, chat_id, title)

            if video_path is None:
                await send_message(chat_id, title + " Бля, не сжалось, сорян, мусорян")
                return

            # with open(video_path, 'rb') as video:
            STATUS_MANAGER.add(chat_id, await send_message(chat_id, title + " Отправляю видос, готовься-cъ"))

            for i in range(10):
                print()

            try:
                translated_title = translate_video_title(title, map_lang(TRANSLATION_LANG))
            except OpenAiQuotaError:
                await send_message(ADMIN_CHAT_ID, "OpenAI quota is empty. Skipping title translation.")
                translated_title = None

            print(translated_title)

            for i in range(10):
                print()

            caption = f"{translated_title}\n\n" if translated_title is not None else ""
            caption += f"{title}\n\nОригинальное видео: {link}"

            print(f"Compiled video path: {video_path}")
            if DEBUG:
                with open("debug_video_path.txt", "w") as f:
                    f.write(video_path)
            await async_send_video(
                            chat_id,
                            video_path,
                            supports_streaming=True,
                            height=video_height,
                            width=video_width,
                            caption=caption,
                            thumbnail=thumbnail
                        )
            if Dir.exists(temp_dir):
                Dir.delete(temp_dir)
        else:
            await send_message(chat_id, title + " Чот пошло не так, бля, сорян, мусорян. ")

    except Exception as e:
        # Get full traceback
        full_error_message = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
        print(f"An unexpected error occurred: {full_error_message}")

        # Send detailed error to ADMIN_CHAT_ID
        try:
            admin_notification_text = f"An error occurred while processing video '{title}' ({link}):\n\n{full_error_message}"
            await send_message(ADMIN_CHAT_ID, admin_notification_text)
        except Exception as admin_send_error:
            print(f"Failed to send error notification to admin: {admin_send_error}")

        # Send user-friendly message to the user
        try:
            await send_message(chat_id, title + " К сожалению, произошла непредвиденная ошибка. Мы уже работаем над её устранением. Пожалуйста, попробуйте позже.")
        except telebot.apihelper.ApiException as user_send_error:
            print(f"Failed to send user-friendly error message to user {chat_id}: {user_send_error}")


async def debug_mode(video_link):
    """
    Debug mode for end-to-end testing without Telegram.
    Simulates receiving a message and processes the video.
    """
    print(f"\n{'='*60}")
    print(f"DEBUG MODE: Processing video")
    print(f"Link: {video_link}")
    print(f"Chat ID: {ADMIN_CHAT_ID}")
    print(f"{'='*60}\n")

    # Create a mock message object
    class MockChat:
        def __init__(self, chat_id):
            self.id = chat_id

    class MockMessage:
        def __init__(self, text, chat_id):
            self.text = text
            self.chat = MockChat(chat_id)
            self.message_id = 1

    mock_message = MockMessage(video_link, ADMIN_CHAT_ID)

    try:
        # Process the message
        await handle_message(mock_message)
    finally:
        # Ensure the client is disconnected in debug mode
        if TELETHON_CLIENT.is_connected():
            await TELETHON_CLIENT.disconnect()

    print(f"\n{'='*60}")
    print(f"DEBUG MODE: Completed!")
    print(f"{'='*60}\n")


async def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Telegram Video Translation Bot')
    parser.add_argument('--debug', type=str, metavar='VIDEO_URL',
                        help='Debug mode: process a video URL without starting the bot')
    parser.add_argument('--debug-chat-id', type=int, default=ADMIN_CHAT_ID,
                        help='Chat ID to use in debug mode (default: ADMIN_CHAT_ID)')

    args = parser.parse_args()

    # Check for debug mode
    if args.debug:
        print("Starting in DEBUG MODE...")
        # Update ADMIN_CHAT_ID if custom chat ID provided
        if args.debug_chat_id != ADMIN_CHAT_ID:
            print(f"Using custom chat ID: {args.debug_chat_id}")
        await debug_mode(args.debug)
        return

    # Normal bot mode
    if "-i" not in OS.args:
        print("Starting Telegram bot in polling mode...")
        skipped_exceptions = (requests.exceptions.ReadTimeout,
                              requests.exceptions.ConnectionError,
                              requests.exceptions.ChunkedEncodingError,
                              telebot.apihelper.ApiException,
                              telebot.apihelper.ApiTelegramException)
        ended = False
        while not ended:
            try:
                await BOT.polling()
                ended = True
            except skipped_exceptions as e:
                print(f"{e} {e.args} {e.with_traceback(e.__traceback__)}... {Time.dotted()}")
                time.sleep(5)


if __name__ == '__main__':
    asyncio.run(main())
