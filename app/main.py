import asyncio
from contextlib import asynccontextmanager
from warnings import warn

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
import logging

from api import status, summarize_and_send_to_group_api, webhook
import models  # noqa
from config import get_settings
from whatsapp import WhatsAppClient
from scheduler import create_summary_scheduler
from summarize_and_send_to_groups import summarize_and_send_to_groups
from whatsapp.init_groups import gather_groups

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Create and configure logger
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=settings.log_level,
    )

    app.state.settings = settings

    app.state.whatsapp = WhatsAppClient(
        settings.whatsapp_host,
        settings.whatsapp_basic_auth_user,
        settings.whatsapp_basic_auth_password,
    )

    if settings.db_uri.startswith("postgresql://"):
        warn("use 'postgresql+asyncpg://' instead of 'postgresql://' in db_uri")
    engine = create_async_engine(
        settings.db_uri,
        pool_size=20,
        max_overflow=40,
        pool_timeout=30,
        pool_pre_ping=True,
        pool_recycle=600,
        future=True,
    )
    async_session = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    async def sync_groups_on_startup() -> None:
        async with async_session() as session:
            try:
                await gather_groups(session, app.state.whatsapp)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    asyncio.create_task(sync_groups_on_startup())

    app.state.db_engine = engine
    app.state.async_session = async_session

    async def run_scheduled_summary() -> None:
        async with async_session() as session:
            try:
                results = await summarize_and_send_to_groups(
                    settings, session, app.state.whatsapp
                )
                logger.info("Scheduled summary run finished: %s", results)
            except Exception:
                logger.exception("Scheduled summary run failed")

    scheduler = create_summary_scheduler(settings, run_scheduled_summary)
    if scheduler:
        scheduler.start()
        for job in scheduler.get_jobs():
            logger.info("Next scheduled summary run: %s", job.next_run_time)

    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)
        await engine.dispose()


# Initialize FastAPI app
app = FastAPI(title="Webhook API", lifespan=lifespan)


app.include_router(webhook.router)
app.include_router(status.router)
app.include_router(summarize_and_send_to_group_api.router)

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    print(f"Running on {settings.host}:{settings.port}")

    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=True)
