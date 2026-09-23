FROM registry.access.redhat.com/ubi10/python-314-minimal@sha256:39659b2e2f54adcdbe66315e10edae56e9a7c79ac6e798edebba340535c9c4a3 AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc /uv /bin/uv
COPY LICENSE /licenses/

ENV \
    UV_PYTHON="/usr/bin/python3.14" \
    # disable uv cache. it doesn't make sense in a container
    UV_NO_CACHE=true

USER root
RUN microdnf install -y make
USER 1001

COPY . .

#
# Test image
#
FROM base AS test
RUN make _test

#
# PyPI publish image
#
FROM test AS pypi
# Secrets are owned by root and are not readable by others :(
USER root
RUN --mount=type=secret,id=app-sre-pypi-credentials/token make -s pypi
