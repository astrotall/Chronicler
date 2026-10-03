import asyncio
import logging

from aiogram import Bot, Dispatcher
from pydantic import ValidationError

from app.bot.handlers import build_router
from app.bot.middlewares import OwnerOnlyMiddleware
from app.config.constants import CONFIG_ERROR_HEADER, LOG_FORMAT
from app.config.settings import Settings
from app.llm.errors import LLMConfigError
from app.llm.factory import validate_provider_keys

logger = logging.getLogger(__name__)


def describe_config_error(error: ValidationError) -> str:
    lines = [
        f"{'.'.join(str(part) for part in problem['loc']).upper()}: {problem['msg']}"
        for problem in error.errors(include_input=False, include_url=False)
    ]
    return "\n".join([CONFIG_ERROR_HEADER, *lines])


def build_dispatcher(settings: Settings) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(OwnerOnlyMiddleware(settings.owner_telegram_ids))
    dispatcher.include_router(build_router())
    return dispatcher


async def run(settings: Settings) -> None:
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    dispatcher = build_dispatcher(settings)
    logger.info("starting polling")
    await dispatcher.start_polling(bot)


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
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
