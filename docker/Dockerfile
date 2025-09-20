FROM alpine:latest
MAINTAINER rix1337

RUN apk add --no-cache \
    python3-dev \
    py3-pip \
    build-base

WORKDIR /app

RUN mkdir -p ~/.config/pip && echo -e "[global]\nbreak-system-packages = true" > ~/.config/pip/pip.conf \
  && pip3 install --upgrade pip \
  && pip3 install wheel

COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . /app
RUN pip3 install --no-cache-dir -e .

VOLUME /config
EXPOSE 8080
ENV PYTHONUNBUFFERED=1
ENV DOCKER="true"
ENV INTERNAL_ADDRESS=""
ENV EXTERNAL_ADDRESS=""
ENV DISCORD=""
ENV HOSTNAMES=""

ENTRYPOINT ["sh", "-c", "quasarr --port=8080 --internal_address=$INTERNAL_ADDRESS --external_address=$EXTERNAL_ADDRESS --discord=$DISCORD --hostnames=$HOSTNAMES"]
