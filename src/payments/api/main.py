from fastapi import FastAPI

from .v1 import payments_router, providers_router, webhooks_router


def create_app() -> FastAPI:
    app = FastAPI(title="payments-gateway", version="0.1.0")
    app.include_router(payments_router, prefix="/v1")
    app.include_router(providers_router, prefix="/v1")
    app.include_router(webhooks_router, prefix="/v1")
    return app


app = create_app()
