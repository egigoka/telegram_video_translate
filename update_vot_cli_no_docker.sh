#!/bin/sh
echo "Install vot-cli"
npm install -g vot-cli

SCRIPT_URL=$(cat ./scripts/link_script.txt)

echo "Downloadind $SCRIPT_URL"
curl -o ./scripts/downloaded_script.sh $SCRIPT_URL

# Ensure the downloaded script is executable
chmod +x ./scripts/downloaded_script.sh

echo "Starting Telegram bot"
python3 ./scripts/telegram_bot.py
echo "Telegram bot exited :-("

while true; do
	sleep 60
	echo "telegram down i guess"
done
