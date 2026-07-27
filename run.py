import uvicorn

if __name__ == "__main__":
    # proxy_headers + forwarded_allow_ips="*": behind a reverse proxy (e.g. Caddy
    # terminating TLS and forwarding plain HTTP internally, as in the public server
    # deployment), the app otherwise has no way to know the original request was
    # HTTPS - Starlette's request.base_url would default to "http://", which ends up
    # baked into every URL the app generates (RSS enclosures, feed self-links, cover
    # art). uvicorn's default forwarded_allow_ips only trusts 127.0.0.1, but the proxy
    # reaches this app over the Docker network, not loopback - "*" trusts the
    # X-Forwarded-Proto header from any peer, which is safe here since the app is
    # never reachable except through that proxy (no published port to the internet).
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
