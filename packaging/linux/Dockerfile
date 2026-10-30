ARG UBUNTU_VERSION=22.04
FROM ubuntu:${UBUNTU_VERSION}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 python3-pip python3-venv python3-gi \
    gir1.2-gtk-3.0 gir1.2-webkit2-4.1 gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good pulseaudio-utils libsndfile1 \
    libsecret-1-0 gnome-keyring dbus-x11 xvfb xauth desktop-file-utils \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /build-env
RUN /build-env/bin/pip install --no-cache-dir 'pip<26' 'setuptools>=68' wheel pytest pytest-asyncio ruff
WORKDIR /src
ENTRYPOINT ["/build-env/bin/python", "packaging/linux/build_deb.py"]
