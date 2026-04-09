import discord
from discord.ext import commands
import os
from typing import Literal

TOKEN = os.getenv("DISCORD_TOKEN")
LEADER_ROLE_ID = 1491911887493923029  # your leader role ID

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
    everyone: bool = False
):
    parts = [toon, str(amount)]

if priority:
    parts.append(priority)

if everyone:
    parts.append("@everyone")

msg = " ".join(parts)

await interaction.response.send_message(
    msg,
    allowed_mentions=discord.AllowedMentions(everyone=True)
)

@bot.tree.command(name="review", description="Tag leaders for bid review")
async def review(interaction: discord.Interaction, reason: str):
    role = interaction.guild.get_role(LEADER_ROLE_ID)

    if role is None:
        await interaction.response.send_message("Role not found in this server")
        return

    await interaction.response.send_message(f"{role.mention} Review needed: {reason}")

bot.run(TOKEN)
