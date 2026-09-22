ARG GO_VERSION=1.27.1
FROM golang:${GO_VERSION}-alpine AS builder

ARG APP_NAME=app
ARG TARGET_PATH=./cmd/api

WORKDIR /app

RUN apk add --no-cache ca-certificates tzdata && \
    adduser -D -u 10001 -s /sbin/nologin -h /app scratchuser

RUN --mount=type=cache,target=/go/pkg/mod/ \
    --mount=type=bind,source=go.sum,target=go.sum \
    --mount=type=bind,source=go.mod,target=go.mod \
    go mod download -x

ARG TARGETOS
ARG TARGETARCH

RUN --mount=type=cache,target=/go/pkg/mod/ \
    --mount=type=cache,target="/root/.cache/go-build" \
    --mount=type=bind,target=. \
    CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build -trimpath -ldflags '-s -w -extldflags "-static"' -o /bin/app ${TARGET_PATH}

FROM scratch
WORKDIR /app

COPY --from=builder /bin/app /app/app
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /usr/share/zoneinfo /usr/share/zoneinfo
COPY --from=builder /etc/passwd /etc/passwd
COPY --from=builder /etc/group /etc/group

USER 10001:10001
STOPSIGNAL SIGINT

ENTRYPOINT ["/app/app"]
