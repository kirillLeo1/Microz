from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

IGNORABLE_DELETE = (
    "message to delete not found",
    "message can't be deleted",
    "chat not found",
)

INVISIBLE = "\u2063"  # невидимий символ (не пустий!)

async def replace_message(
    message: Message,
    text: str | None = None,
    reply_markup=None,
    disable_web_page_preview: bool | None = None,
    photo: str | None = None,
):
    """Видаляє старе повідомлення й шле нове. Ніколи не відправляє пустий текст."""
    try:
        await message.delete()
    except TelegramBadRequest as e:
        low = str(e).lower()
        if not any(x in low for x in IGNORABLE_DELETE):
            raise

    safe_text = (text if (text and text.strip()) else INVISIBLE)

    if photo:
        return await message.answer_photo(
            photo=photo,
            caption=safe_text,
            reply_markup=reply_markup,
        )
    else:
        return await message.answer(
            safe_text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )

