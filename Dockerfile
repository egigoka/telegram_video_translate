FROM node:18-alpine

WORKDIR /usr/src/app

RUN apk update

RUN apk add --no-cache python3
RUN apk add --no-cache py3-pip
RUN apk add --no-cache git
RUN apk add --no-cache py3-psutil

RUN npm install -g vot-cli

RUN pip install git+https://github.com/egigoka/commands --break-system-packages
RUN pip install git+https://github.com/egigoka/telegrame --break-system-packages
RUN pip install pytelegrambotapi --break-system-packages
RUN pip install requests --break-system-packages

RUN apk add --no-cache yt-dlp
RUN apk add --no-cache jq
RUN apk add --no-cache ffmpeg
RUN apk add --no-cache curl
RUN apk add --no-cache bash
RUN apk add --no-cache ncurses

COPY scripts /usr/src/app/scripts

RUN chmod +x /usr/src/app/scripts/update_vot_cli.sh
