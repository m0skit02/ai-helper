FROM golang:1.25.8 AS build

WORKDIR /src

COPY go.mod go.sum ./
RUN sh -c 'n=0; until [ "$n" -ge 5 ]; do go mod download && exit 0; n=$((n + 1)); echo "go mod download retry $n/5"; sleep 3; done; exit 1'

COPY cmd ./cmd

RUN CGO_ENABLED=0 GOOS=linux go build -o /out/tools-service ./cmd/tools-service

FROM gcr.io/distroless/static-debian12

WORKDIR /app

COPY --from=build /out/tools-service /app/tools-service

EXPOSE 8080

ENTRYPOINT ["/app/tools-service"]
