import os
import asyncio
from collections import defaultdict
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL = "@iceageclips"
ADMIN_ID = 2001525037 

SLEEP_SEC = 1.2  # flooddan saqlash (kanalga ketma-ket yuborishda pauza)
TMP_DIR = Path("tmp_dl")
TMP_DIR.mkdir(exist_ok=True)

if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_TOKEN_HERE":
    raise RuntimeError("BOT_TOKEN ni environment orqali bering yoki kodga token qo'ying!")

dp = Dispatcher()

# user_id -> key -> { "video": file_id OR local_path, "txt": text }
stash = defaultdict(lambda: defaultdict(dict))

# ✅ har bir user uchun flush lock (concurrent flush -> dublikat yuborishni to‘xtatadi)
flush_locks = defaultdict(asyncio.Lock)


# =========================
# HELPERS
# =========================
def is_admin(m: Message) -> bool:
    # ADMIN_ID ni None qilsangiz, hamma ishlatishi mumkin bo‘ladi
    return (ADMIN_ID is None) or (m.from_user and m.from_user.id == ADMIN_ID)


def key_from_filename(filename: str) -> str:
    # "video1.mp4" -> "video1"
    return Path(filename).stem.lower().strip()


async def flush(uid: int, bot: Bot):
    """
    Bir xil key uchun video+txt to'liq bo'lsa, kanalga yuboradi.
    Lock dublikat yuborishni to'xtatadi.
    """
    async with flush_locks[uid]:
        ready_keys = []
        for k, v in stash[uid].items():
            if "video" in v and "txt" in v:
                ready_keys.append(k)

        for k in sorted(ready_keys):
            v = stash[uid].get(k)
            if not v:
                continue

            caption = (v.get("txt") or "").strip()[:1024]
            video_obj = v.get("video")

            try:
                # video_obj file_id bo'lishi ham mumkin, local path bo'lishi ham mumkin
                if isinstance(video_obj, Path):
                    await bot.send_video(
                        chat_id=CHANNEL,
                        video=FSInputFile(str(video_obj)),
                        caption=caption,
                    )
                    # yuborilgandan keyin tmp faylni o'chiramiz
                    try:
                        video_obj.unlink()
                    except Exception:
                        pass
                else:
                    await bot.send_video(
                        chat_id=CHANNEL,
                        video=video_obj,
                        caption=caption,
                    )

                # ✅ KeyError va race bo'lmasin
                stash[uid].pop(k, None)

                await asyncio.sleep(SLEEP_SEC)

            except Exception:
                # yuborishda xato bo'lsa, stashni o'chirmaymiz (keyin qayta urinishi mumkin)
                # xohlasangiz shu yerga log qo'shing
                break


# =========================
# HANDLERS
# =========================
@dp.message(F.video)
async def on_video(m: Message, bot: Bot):
    if not is_admin(m):
        return

    # Agar Telegram video fayl nomini bermasa, unique id bilan key yasaymiz
    fname = m.video.file_name or f"{m.video.file_unique_id}.mp4"
    k = key_from_filename(fname)

    stash[m.from_user.id][k]["video"] = m.video.file_id
    await flush(m.from_user.id, bot)


@dp.message(F.document)
async def on_document(m: Message, bot: Bot):
    if not is_admin(m):
        return

    doc = m.document
    if not doc or not doc.file_name:
        return

    name = doc.file_name
    low = name.lower()

    # 1) TXT bo'lsa: ichini o'qiymiz
    if low.endswith(".txt"):
        k = key_from_filename(name)
        file = await bot.get_file(doc.file_id)
        tmp = TMP_DIR / f"{m.from_user.id}_{k}.txt"
        await bot.download_file(file.file_path, destination=tmp)

        text = tmp.read_text(encoding="utf-8", errors="ignore").strip()
        tmp.unlink(missing_ok=True)

        stash[m.from_user.id][k]["txt"] = text
        await flush(m.from_user.id, bot)
        return

    # 2) MP4/MOV/MKV bo'lsa: video deb qabul qilamiz
    if low.endswith((".mp4", ".mov", ".mkv")):
        k = key_from_filename(name)

        # Eng ishonchli yo'l: download qilib, kanalga FSInputFile bilan yuboramiz
        file = await bot.get_file(doc.file_id)
        suffix = Path(name).suffix.lower()
        tmp_video = TMP_DIR / f"{m.from_user.id}_{k}{suffix}"
        await bot.download_file(file.file_path, destination=tmp_video)

        stash[m.from_user.id][k]["video"] = tmp_video
        await flush(m.from_user.id, bot)
        return

    # boshqa fayllarni e'tiborsiz qoldiramiz
    return


# =========================
# RUN
# =========================
async def main():
    bot = Bot(BOT_TOKEN)
    # ✅ restartdan keyin eski update'lar qayta kelib, dublikat yubormasligi uchun
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())

