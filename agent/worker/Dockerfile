# The official multiarch manifest pins both amd64 and arm64 base variants.
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS certificates
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587

# Bootstrap public CA trust from a pinned official image. Package indexes and
# packages still require Debian archive signatures, and HTTPS remains verified.
COPY --from=certificates /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

RUN --mount=type=secret,id=janus-build-ca,mode=0444 \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && JANUS_CA_FILE=/etc/ssl/certs/ca-certificates.crt \
    && if [ -s /run/secrets/janus-build-ca ]; then JANUS_CA_FILE=/run/secrets/janus-build-ca; fi \
    && apt-get -o Acquire::https::CaInfo="$JANUS_CA_FILE" update --error-on=any \
    && apt-get -o Acquire::https::CaInfo="$JANUS_CA_FILE" install -y --no-install-recommends gcc libc6-dev python3 libseccomp2 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /work \
    && chown 65532:65532 /work \
    && chmod 700 /work

COPY --chown=0:0 runner.py /opt/janus/runner.py
RUN chmod 444 /opt/janus/runner.py

ENV HOME=/work LANG=C.UTF-8
USER 65532:65532
WORKDIR /work
ENTRYPOINT ["/usr/bin/python3", "-I", "-B", "/opt/janus/runner.py"]
