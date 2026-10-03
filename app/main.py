import asyncio
import logging

from aiogram import Bot, Dispatcher
from pydantic import ValidationError

from app.bot.flow import BOT_CONTEXT_KEY, BotContext
from app.bot.handlers import build_router
from app.bot.jobs import JobRunner
from app.bot.middlewares import OwnerOnlyMiddleware
from app.config.constants import CONFIG_ERROR_HEADER, LOG_FORMAT, LLMStep
from app.config.settings import Settings
from app.llm.errors import LLMConfigError
from app.llm.factory import LLMClientFactory, build_http_client, validate_provider_keys
from app.research.factory import build_sources, log_source_availability
from app.research.http import build_research_http_client
from app.research.source import ResearchSource
from app.services.pipeline import Pipeline, PipelineClients, PipelineLimits
from app.services.research import ResearchLimits, ResearchService
from app.services.run_store import InMemoryRunStore

logger = logging.getLogger(__name__)


def describe_config_error(error: ValidationError) -> str:
    lines = [
        f"{'.'.join(str(part) for part in problem['loc']).upper()}: {problem['msg']}"
        for problem in error.errors(include_input=False, include_url=False)
    ]
    return "\n".join([CONFIG_ERROR_HEADER, *lines])


def build_pipeline(
    settings: Settings, factory: LLMClientFactory, sources: list[ResearchSource]
) -> Pipeline:
    return Pipeline(
        PipelineClients(
            planner=factory.get_client(LLMStep.QUERY_PLANNING),
            extractor=factory.get_client(LLMStep.FACT_EXTRACTION),
            writer=factory.get_client(LLMStep.WRITING),
            critic=factory.get_client(LLMStep.STYLE_CRITIQUE),
        ),
        ResearchService(sources, ResearchLimits.from_settings(settings)),
        InMemoryRunStore(settings.state_max_runs),
        PipelineLimits.from_settings(settings),
    )


def build_dispatcher(settings: Settings, pipeline: Pipeline) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(OwnerOnlyMiddleware(settings.owner_telegram_ids))
    dispatcher.include_router(build_router())
    dispatcher[BOT_CONTEXT_KEY] = BotContext(
        pipeline=pipeline, jobs=JobRunner(), timeout_seconds=settings.pipeline_timeout_seconds
    )
    return dispatcher


async def run(settings: Settings) -> None:
    async with (
        build_http_client(settings) as llm_http,
        build_research_http_client(settings) as research_http,
    ):
        pipeline = build_pipeline(
            settings, LLMClientFactory(settings, llm_http), build_sources(settings, research_http)
        )
        dispatcher = build_dispatcher(settings, pipeline)
        context: BotContext = dispatcher[BOT_CONTEXT_KEY]
        bot = Bot(token=settings.telegram_bot_token.get_secret_value())
        logger.info("starting polling")
        try:
            await dispatcher.start_polling(bot, handle_as_tasks=True)
        finally:
            await context.jobs.cancel_all()


def load_settings() -> Settings:
    try:
        settings = Settings()
    except ValidationError as error:
        raise SystemExit(describe_config_error(error)) from error
    try:
        validate_provider_keys(settings)
    except LLMConfigError as error:
        raise SystemExit("\n".join([CONFIG_ERROR_HEADER, str(error)])) from error
    return settings


def main() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level, format=LOG_FORMAT)
    log_source_availability(settings)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
