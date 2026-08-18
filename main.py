import asyncio
import datetime
import logging
import os
import random
from aiohttp import web

import discord
from discord.ext import commands

COMMAND_PREFIX = "!"
POLL_CHANNEL_ID = 1445760622762655898
RESULT_CHANNEL_ID = 1396886120356118718  # 指定の投稿先チャネルID

# 4人組スタートの基準日（2026年8月13日）
BASE_DATE = datetime.date(2026, 8, 13)

# 外部からの誤呼び出しを防ぐためのトークン
API_SECRET_TOKEN = os.environ.get("API_SECRET_TOKEN", "default_secret_key")


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


async def run_team_division(bot: commands.Bot, size: int = None) -> tuple[bool, str]:
    """チーム分けを実行し、Embed形式で指定チャネルへ送信するコア関数"""
    try:
        # 人数が指定されていない場合は自動判定（2週間周期）
        if size is None:
            today = datetime.date.today()
            days_diff = (today - BASE_DATE).days
            period_index = days_diff // 14
            size = 2 if (period_index % 2 == 1) else 4

        if size < 1:
            return False, "チーム人数は1人以上に指定してください。"

        # アンケートメッセージの取得
        channel = bot.get_channel(POLL_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(POLL_CHANNEL_ID)

        target_msg = None
        async for message in channel.history(limit=200):
            if message.poll:
                target_msg = message
                break

        if target_msg is None or not target_msg.poll:
            return False, "アンケートチャネルに有効な投票メッセージが見つかりませんでした。"

        # 投票回答の特定
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

        # ユーザー取得とシャッフル
        raw_users = await get_poll_answer_users(target_answer)
        members = [u.mention for u in raw_users if not u.bot]

        if not members:
            target_text = getattr(target_answer, "text", "参加")
            return False, f"「{target_text}」に投票したユーザーがいません。"

        random.shuffle(members)
        teams = [members[i:i + size] for i in range(0, len(members), size)]

        # Embedメッセージの整形
        target_text = getattr(target_answer, "text", "参加")
        embed = discord.Embed(
            title="🎲 チーム分け結果",
            color=0x3498db
        )

        # 条件のミニカード表示（インライン）
        embed.add_field(name="形式", value=f"{size}人組", inline=True)
        embed.add_field(name="対象", value=target_text, inline=True)
        embed.add_field(name="参加人数", value=f"計 {len(members)} 名", inline=True)

        # チームごとの一覧（引用マークで確実にインデント＋透明フィールドで改行）
        for i, t in enumerate(teams, 1):
            is_full = (len(t) == size)
            icon = "👥" if is_full else "⚠️"
            team_title = f"{icon} チーム {i}" if is_full else f"{icon} チーム {i}（余り {len(t)}名）"
            
            # 引用記号 (>) を使って文字を下げる（インデント化）
            member_list = "\n".join([f"> {m}" for m in t])
            
            embed.add_field(
                name=team_title,
                value=member_list,
                inline=False
            )
            
            # 最後のチーム以外には「透明な空フィールド」を入れてチーム間に空行（間隔）を作る
            if i < len(teams):
                embed.add_field(name="\u200b", value="\u200b", inline=False)

        # 指定チャネルへの投稿
        dest_channel = bot.get_channel(RESULT_CHANNEL_ID)
        if dest_channel is None:
            dest_channel = await bot.fetch_channel(RESULT_CHANNEL_ID)

        await dest_channel.send(embed=embed)
        return True, "チーム分け結果を送信しました。"

    except Exception as e:
        logging.error("チーム分け実行エラー: %s", e)
        return False, f"エラーが発生しました: {e}"


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
        size = None
        if arg1 is not None and arg1.isdigit() and len(arg1) <= 2:
            size = int(arg1)
        elif arg2 is not None:
            size = arg2

        success, msg = await run_team_division(bot, size=size)
        if not success:
            await ctx.send(msg)

    return bot


async def start_web_server(bot: commands.Bot):
    """APIリクエストを受け付けるWebサーバー"""
    async def handle_health(request):
        return web.Response(text="Bot is running!")

    async def handle_api_team(request):
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {API_SECRET_TOKEN}":
            return web.json_response({"status": "error", "message": "Unauthorized"}, status=401)

        success, msg = await run_team_division(bot)
        if success:
            return web.json_response({"status": "success", "message": msg})
        else:
            return web.json_response({"status": "error", "message": msg}, status=400)

    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_post("/api/team", handle_api_team)

    runner = web.AppRunner(app)
    await runner.setup()
    port = 10000
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

    bot = create_bot()
    await start_web_server(bot)
    await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main_async())
