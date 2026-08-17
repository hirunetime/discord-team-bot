import asyncio
import logging
import os
import random
from aiohttp import web

import discord
from discord.ext import commands

COMMAND_PREFIX = "!"
POLL_CHANNEL_ID = 1445760622762655898


async def get_poll_answer_users(answer) -> list[discord.User | discord.Member]:
    users = []
    attr_names = ["users", "voters", "fetch_users", "fetch_voters"]

    for attr_name in attr_names:
        if hasattr(answer, attr_name):
            attr = getattr(answer, attr_name)
            try:
                if callable(attr):
                    res = attr()
                    if hasattr(res, "__aiter__"):
                        async for u in res:
                            users.append(u)
                        return users
                    elif hasattr(res, "__await__"):
                        fetched = await res
                        if isinstance(fetched, (list, tuple)):
                            return list(fetched)
                    elif isinstance(res, (list, tuple)):
                        return list(res)
                elif hasattr(attr, "__aiter__"):
                    async for u in attr:
                        users.append(u)
                    return users
                elif isinstance(attr, (list, tuple)):
                    return list(attr)
            except Exception as e:
                logging.warning("Failed to fetch via %s: %s", attr_name, e)

    available_attrs = [a for a in dir(answer) if not a.startswith("_")]
    raise RuntimeError(
        f"投票者の取得方法が見つかりませんでした。(確認された属性: {available_attrs})"
    )


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(
        command_prefix=COMMAND_PREFIX,
        intents=intents,
        description="チーム分け Discord Bot",
    )

    @bot.event
    async def on_ready() -> None:
        if bot.user is not None:
            logging.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)

    @bot.command()
    async def team(
        ctx: commands.Context[commands.Bot],
        arg1: str = None,
        arg2: int = None
    ) -> None:
        try:
            target_msg = None
            size = 2

            if arg1 is None:
                size = 2
            elif arg1.isdigit() and len(arg1) <= 2:
                size = int(arg1)
            else:
                try:
                    converter = commands.MessageConverter()
                    target_msg = await converter.convert(ctx, arg1)
                except Exception:
                    pass
                if arg2 is not None:
                    size = arg2

            if size < 1:
                await ctx.send("チーム人数は1人以上に指定してください。")
                return

            if target_msg is None:
                channel = bot.get_channel(POLL_CHANNEL_ID)
                if channel is None:
                    channel = await bot.fetch_channel(POLL_CHANNEL_ID)

                async for message in channel.history(limit=50):
                    if message.poll:
                        target_msg = message
                        break

            if target_msg is None or not target_msg.poll:
                await ctx.send("アンケートチャネルに有効な投票メッセージが見つかりませんでした。")
                return

            target_answer = None
            for ans in target_msg.poll.answers:
                text = getattr(ans, "text", "") or ""
                if not text and hasattr(ans, "media") and hasattr(ans.media, "text"):
                    text = ans.media.text or ""

                if "参加" in text and "不参加" not in text:
                    target_answer = ans
                    break

            if not target_answer:
                target_answer = target_msg.poll.answers[0]

            raw_users = await get_poll_answer_users(target_answer)
            members = [u.mention for u in raw_users if not u.bot]

            if not members:
                target_text = getattr(target_answer, "text", "参加")
                await ctx.send(f"「{target_text}」に投票したユーザーがいません。")
                return

            random.shuffle(members)
            teams = [members[i:i + size] for i in range(0, len(members), size)]

            target_text = getattr(target_answer, "text", "参加")
            result = f"**【チーム分け結果】（{size}人組 / 対象: {target_text} / 計{len(members)}名）**\n"
            for i, t in enumerate(teams, 1):
                if len(t) == size:
                    result += f"**チーム {i}**: {' & '.join(t)}\n"
                else:
                    result += f"**チーム {i}（余り {len(t)}名）**: {' & '.join(t)}\n"

            await ctx.send(result)

        except Exception as e:
            await ctx.send(f"エラーが発生しました: {e}")

    return bot


async def start_web_server():
    """Renderのヘルスチェックをパスするための軽量Webサーバー"""
    async def handle(request):
        return web.Response(text="Bot is running!")

    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main_async() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not configured.")

    await start_web_server()
    bot = create_bot()
    await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main_async())
