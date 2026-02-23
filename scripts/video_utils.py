import os
import ffmpeg
import subprocess
import json
from commands import Video, KiB, MiB

MAX_VIDEO_SIZE = 2 * 1024 * 1024 * 1024
MIN_AUDIO_BITRATE = 32 * KiB
MAX_AUDIO_BITRATE = 256 * KiB
ADDITIONAL_VIDEO_BITRATE_MULTIPLIER = 0.95

def get_new_video_info(video_path):
    video_width, video_height = Video.get_resolution(video_path)

    new_width = video_width
    new_height = video_height
    if video_height > 1080:
        new_height = 1080
        new_width = int(video_width * new_height / video_height)

    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=bit_rate", "-of", "json", video_path], capture_output=True, text=True)
    audio_bitrate = MAX_AUDIO_BITRATE
    if probe.stdout:
        audio_bitrate = int(json.loads(probe.stdout)['streams'][0]['bit_rate'])

    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=bit_rate", "-of", "json", video_path], capture_output=True, text=True)
    video_bitrate = 3000
    if probe.stdout:
        video_bitrate = int(json.loads(probe.stdout)['streams'][0]['bit_rate'])

    video_length = Video.get_length(video_path)

    if video_length > 1800 and video_height > 720:
        new_height = 720
        new_width = int(video_width * new_height / video_height)

    if video_length > 3600 and video_height > 480:
        new_height = 480
        new_width = int(video_width * new_height / video_height)

    if video_length > 7200 and video_height > 360:
        new_height = 360
        new_width = int(video_width * new_height / video_height)

    if video_length > 21600 and video_height > 240:
        new_height = 240
        new_width = int(video_width * new_height / video_height)

    if new_width % 2 != 0:
        new_width += 1

    audio_size = audio_bitrate * video_length / 8

    new_audio_bitrate = audio_bitrate  # save if it's possible
    if audio_size * 2 > 1 / 4 * MAX_VIDEO_SIZE:  # if audio size is too large
        new_audio_bitrate = int(1 / 4 * MAX_VIDEO_SIZE / video_length)
    new_audio_bitrate = MIN_AUDIO_BITRATE if new_audio_bitrate < MIN_AUDIO_BITRATE else new_audio_bitrate
    new_audio_bitrate = MAX_AUDIO_BITRATE if new_audio_bitrate > MAX_AUDIO_BITRATE else new_audio_bitrate

    new_audio_size = new_audio_bitrate * video_length / 8
    new_audio_size *= 2  # there will be two audio tracks

    new_video_size = MAX_VIDEO_SIZE - new_audio_size
    new_video_bitrate = int(new_video_size / video_length * 8)
    new_video_bitrate = video_bitrate if video_bitrate < new_video_bitrate else new_video_bitrate

    # It's still not perfect, but it's better than nothing
    new_video_bitrate = int(new_video_bitrate * ADDITIONAL_VIDEO_BITRATE_MULTIPLIER)
    return new_video_bitrate, new_audio_bitrate, new_width, new_height
