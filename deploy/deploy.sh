#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_file=/etc/trading-app/trading-app.env

[ -r "$env_file" ] || {
    echo "missing deployment environment: $env_file" >&2
    exit 1
}

cd "$repo_dir"

docker compose --env-file "$env_file" config --quiet
nginx -t
docker compose --env-file "$env_file" build app
docker compose --env-file "$env_file" up -d --remove-orphans
systemctl reload nginx

container_id=$(docker compose --env-file "$env_file" ps -q app)
[ -n "$container_id" ] || {
    echo "application container was not created" >&2
    exit 1
}

health=starting
attempt=0
while [ "$attempt" -lt 60 ]; do
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")
    case "$health" in
        healthy)
            break
            ;;
        exited|dead)
            docker compose --env-file "$env_file" logs --no-color app >&2
            exit 1
            ;;
    esac
    attempt=$((attempt + 1))
    sleep 2
done

[ "$health" = healthy ] || {
    docker compose --env-file "$env_file" logs --no-color app >&2
    exit 1
}

curl -fsS --max-time 10 http://127.0.0.1:8000/health >/dev/null
echo "trading-app deployed: $container_id"
