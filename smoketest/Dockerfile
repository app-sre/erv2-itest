FROM busybox:latest@sha256:fd7dc98638c8e305f4dc34e979f1c0fdfdcaeb0fbf8fcff77ae834b6da3d7e6e
COPY fake_module.sh /fake_module.sh
RUN chmod +x /fake_module.sh
ENTRYPOINT ["/fake_module.sh"]
