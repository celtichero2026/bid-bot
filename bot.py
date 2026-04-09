import discord
from discord.ext import commands
import os
from typing import Literal

TOKEN = os.getenv("DISCORD_TOKEN")
LEADER_ROLE_ID = 123456789012345678  # replace with your leader role ID

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="bid", description="Post a bid")
async def bid(
    interaction: discord.Interaction,
    toon: str,
    amount: int,
    priority: Literal["Main", "Alt"] = None,
    m1: discord.Member = None,
    m2: discord.Member = None,
    m3: discord.Member = None,
    m4: discord.Member = None,
    m5: discord.Member = None,
    m6: discord.Member = None,
    m7: discord.Member = None,
    m8: discord.Member = None,
    m9: discord.Member = None,
    m10: discord.Member = None
):
    mentions = [m.mention for m in [m1, m2, m3, m4, m5, m6, m7, m8, m9, m10] if m]

    parts = [toon, str(amount)]

    if priority:
        parts.append(priority)

    if mentions:
        parts.extend(mentions)

    msg = " ".join(parts)

    await interaction.response.send_message(msg)

@bot.tree.command(name="review", description="Tag leaders for bid review")
async def review(
    interaction: discord.Interaction,
    reason: str
):
    leader_mention = f"<@&{LEADER_ROLE_ID}>"
    msg = f"{leader_mention} Review needed: {reason}"
    await interaction.response.send_message(msg)

bot.run(TOKEN)
