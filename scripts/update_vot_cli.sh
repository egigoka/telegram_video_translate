#!/bin/sh
echo "Install vot-cli"
npm install -g vot-cli

SCRIPT_URL=$(cat /usr/src/app/scripts/link_script.txt)

echo "Downloadind $SCRIPT_URL"
curl -o /usr/src/app/scripts/downloaded_script.sh $SCRIPT_URL

# Ensure the downloaded script is executable
chmod +x /usr/src/app/scripts/downloaded_script.sh

echo "Starting Telegram bot"
python3 ./scripts/telegram_bot.py
echo "Telegram bot exited :-("
