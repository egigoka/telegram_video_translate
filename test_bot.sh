#!/bin/bash

VIDEO_URL="https://youtube.com/shorts/TkQ4ok3vonQ"
ADMIN_CHAT_ID="5328715"
OUTPUT_FILE="debug_video_path.txt"
PROFILE_OUTPUT="profile_output.prof"

# Kill any existing debug bot instance
pkill -9 -f "python3.*telegram_bot.*debug" 2>/dev/null

# Clean up previous output files
if [ -f "$OUTPUT_FILE" ]; then
    rm "$OUTPUT_FILE"
fi
if [ -f "$PROFILE_OUTPUT" ]; then
    rm "$PROFILE_OUTPUT"
fi

echo "Running telegram_bot.py in debug mode with cProfile..."
timeout 1200 python3 -m cProfile -o "$PROFILE_OUTPUT" scripts/telegram_bot.py --debug "$VIDEO_URL" --debug-chat-id "$ADMIN_CHAT_ID"

echo "Profiling complete. Analyzing results..."
python3 -c "import pstats; p = pstats.Stats('$PROFILE_OUTPUT'); p.sort_stats('cumulative').print_stats(20)"

if [ ! -f "$OUTPUT_FILE" ]; then
    echo "Error: The script did not create the output file '$OUTPUT_FILE'."
    exit 1
fi

VIDEO_PATH=$(cat "$OUTPUT_FILE")

if [ -z "$VIDEO_PATH" ]; then
    echo "Error: Could not read video path from '$OUTPUT_FILE'."
    rm "$OUTPUT_FILE"
    exit 1
fi

echo "Extracted video path: $VIDEO_PATH"
echo "Checking video with ffprobe..."
ffprobe "$VIDEO_PATH"

# Clean up the output file
rm "$OUTPUT_FILE"
