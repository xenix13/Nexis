# Image Docker de Nexis : docker compose up -d (voir docker-compose.yml et le README).
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 \
    SEANCE_HOST=0.0.0.0 SEANCE_PORT=8420 SEANCE_DATA=/data SEANCE_STATIC=/opt/seance/static SEANCE_DOCKER=1 \
    NVIDIA_DRIVER_CAPABILITIES=compute,video,utility

# ffmpeg d'Ubuntu (NVENC, VAAPI, QSV compilés) ; pilotes VAAPI AMD, et Intel (n'existe qu'en amd64)
RUN apt-get update \
 && INTEL=$([ "$(dpkg --print-architecture)" = amd64 ] && echo intel-media-va-driver || true) \
 && apt-get install -y --no-install-recommends python3 ffmpeg ca-certificates mesa-va-drivers $INTEL \
 && rm -rf /var/lib/apt/lists/* \
 && (userdel -r ubuntu 2>/dev/null || true) \
 && useradd --uid 1000 --user-group --home-dir /data --shell /usr/sbin/nologin seance \
 && mkdir -p /data && chown seance:seance /data

WORKDIR /opt/seance
COPY seance ./seance
COPY static ./static
RUN cd static/vendor && sha256sum -c --quiet hls.min.js.sha256 && cd qrcode && sha256sum -c --quiet qrcode.mjs.sha256

USER seance
VOLUME /data
EXPOSE 8420
HEALTHCHECK --interval=30s --timeout=6s --start-period=20s \
  CMD python3 -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/status' % os.environ.get('SEANCE_PORT', '8420'), timeout=5)"
CMD ["python3", "-m", "seance"]
