#!/bin/sh
if [ -f /run/secrets/codex_auth ]; then
	mkdir -p /root/.codex
	cp /run/secrets/codex_auth /root/.codex/auth.json
	chmod 600 /root/.codex/auth.json
fi

echo "Install vot-cli"
npm install -g vot-cli@2.1.0

SCRIPT_URL=$(cat /usr/src/app/scripts/link_script.txt)

echo "Downloadind $SCRIPT_URL"
curl -o /usr/src/app/scripts/downloaded_script.sh $SCRIPT_URL

# Ensure the downloaded script is executable
chmod +x /usr/src/app/scripts/downloaded_script.sh

echo "Starting Telegram bot"
python3 ./scripts/telegram_bot.py
echo "Telegram bot exited :-("

while true; do
	sleep 60
	echo "telegram down i guess"
done
