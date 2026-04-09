ARG BASE_IMAGE="ubuntu:24.04"
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-c"]

# Time zone
ARG TIMEZONE="Asia/Tokyo"
RUN echo "timezone=${TIMEZONE}"
ENV TZ=$TIMEZONE
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone

# Prepare apt
RUN apt-get update && \
    apt-get install -y apt-file && \
    apt-file update

# Install Build tools
RUN apt-get update && \
    apt-get install -y \
      build-essential \
      software-properties-common

# Install Basic tools
RUN apt-get update && \
    apt-get install -y \
      git \
      gh \
      wget \
      curl \
      vim \
      tmux \
      x11-apps \
      rsync \
      tree \
      zip \
      unzip \
      jq \
      libnss-mdns

# Japanese environment
ENV LANGUAGE=ja_JP.UTF-8
ENV LANG=ja_JP.UTF-8
RUN apt-get update && \
    apt-get install -y --no-install-recommends locales && \
    apt-get install -y --no-install-recommends fonts-ipafont fonts-noto-cjk && \
    locale-gen ja_JP.UTF-8

# Install python
ARG PYTHON_VERSION="3.12"
RUN apt-get update && \
    apt-get install -y \
      python${PYTHON_VERSION} \
      python${PYTHON_VERSION}-dev \
      python${PYTHON_VERSION}-venv \
      python3-pip && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python${PYTHON_VERSION} 1 && \
    update-alternatives --set python3 /usr/bin/python${PYTHON_VERSION}

# Install Built-in GUI
RUN apt-get update && \
    apt-get install -y libgtk-3-dev python3-tk

# Create venv (as root, but make it accessible to user later)
RUN mkdir -p /opt/venv && \
    python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install nodejs
ARG NODE_VERSION="22"
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - && \
    apt-get update && apt-get install -y nodejs

# Set user name from argument
ARG USERNAME="user"
ARG USER_UID=1000
ARG USER_GID=1000
RUN echo "user=${USERNAME}"

# Install sudo
RUN apt-get update && \
    apt-get install -y \
      sudo

# Create user and set permissions
RUN userdel -rf $(getent passwd ${USER_UID} | cut -d: -f1) 2>/dev/null || true && \
    groupdel $(getent group ${USER_GID} | cut -d: -f1) 2>/dev/null || true && \
    groupadd -g ${USER_GID} ${USERNAME} && \
    useradd -m -u ${USER_UID} -g ${USER_GID} -G sudo,video,audio -s /bin/bash ${USERNAME} && \
    echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} && \
    chmod 0440 /etc/sudoers.d/${USERNAME}

# Change owner of venv to USER
RUN chown -R ${USERNAME}:${USERNAME} /opt/venv

# Switch to user
USER ${USERNAME}
WORKDIR /home/${USERNAME}
ENV HOME=/home/$USERNAME

# ========================================
# aido framework dependencies
# ========================================
RUN python3 -m pip install --no-cache-dir --upgrade pip
RUN python3 -m pip install --no-cache-dir pyyaml fastapi uvicorn websockets watchdog pytest

# Install llm-agents
RUN echo 20260408
RUN sudo npm install -g @google/gemini-cli
RUN sudo npm install -g @anthropic-ai/claude-code
RUN sudo npm install -g claudeignore
RUN sudo npm install -g @openai/codex
