FROM scratch AS fixture
COPY bin/fixture /fixture
USER 65532:65532
ENTRYPOINT ["/fixture"]

FROM scratch AS probe
COPY bin/probe /probe
USER 65532:65532
ENTRYPOINT ["/probe"]
