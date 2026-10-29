FROM busybox:latest@sha256:a46257e06c82ad85242d0d38077d0a52d9896389ca60a1a59d3463db586e5fa4
COPY fake_module.sh /fake_module.sh
RUN chmod +x /fake_module.sh
ENTRYPOINT ["/fake_module.sh"]
