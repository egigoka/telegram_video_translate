FROM node:18-alpine

WORKDIR /usr/src/app

COPY scripts /usr/src/app/scripts
RUN chmod +x /usr/src/app/scripts/update_vot_cli.sh
RUN cp /usr/src/app/scripts/session.session /usr/src/app/session.session

RUN npm install -g vot-cli

RUN apk update
RUN apk add --no-cache python3
RUN apk add --no-cache py3-pip
RUN apk add --no-cache git
RUN apk add --no-cache py3-psutil
RUN apk add --no-cache py3-cffi
RUN apk add --no-cache dav1d
RUN apk add --no-cache mesa-va-gallium mesa-dri-gallium libva libva-utils
RUN apk add --no-cache deno
RUN pip install yt-dlp --break-system-packages
RUN apk add --no-cache jq
RUN apk add --no-cache ffmpeg
RUN apk add --no-cache curl
RUN apk add --no-cache bash
RUN apk add --no-cache ncurses
RUN apk add --no-cache shadow

# maybe useful
RUN addgroup -g 487 render
RUN usermod -aG render root

# debug
RUN apk add --no-cache fish tree shadow micro eudev

RUN apk add --no-cache intel-media-driver intel-media-sdk

RUN pip install git+https://github.com/egigoka/commands --break-system-packages 
RUN pip install git+https://github.com/egigoka/telegrame --break-system-packages
RUN apk add --no-cache py3-opencv
RUN pip install moviepy --no-deps --break-system-packages
RUN pip install pytelegrambotapi --break-system-packages
RUN pip install requests --break-system-packages
RUN pip install ffmpeg-python --break-system-packages
RUN pip install telethon --break-system-packages
RUN pip install openai --break-system-packages
RUN pip install aiohttp --break-system-packages
