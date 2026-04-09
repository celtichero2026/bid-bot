import discord
from discord.ext import commands
import os

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="bid", description="Post a bid")
async def bid(interaction: discord.Interaction, toon: str, amount: int, user: discord.Member):
    await interaction.response.send_message(f"{toon} {amount} {user.mention}")

TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
