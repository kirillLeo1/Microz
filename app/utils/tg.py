from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

IGNORABLE_DELETE = (
    "message to delete not found",
    "message can't be deleted",
    "chat not found",
)

async def replace_message(
    message: Message,
    text: str | None = None,
    reply_markup=None,
    disable_web_page_preview: bool | None = None,
    photo: str | None = None,
):
    """Видаляє старе повідомлення й шле нове в той самий чат."""
    try:
        await message.delete()
    except TelegramBadRequest as e:
        low = str(e).lower()
        if not any(x in low for x in IGNORABLE_DELETE):
            # якщо причина інша — пробросимо, щоб не хавати справжні помилки
            raise

    if photo:
        # якщо раптом захочеш відправляти картинку
        return await message.answer_photo(
            photo=photo,
            caption=text or "",
            reply_markup=reply_markup,
        )
    else:
        return await message.answer(
            text or "",
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
