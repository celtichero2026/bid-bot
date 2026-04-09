import discord
from discord.ext import commands
import os

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = 1414539813473878141  # your server ID

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    await bot.tree.sync(guild=guild)
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="bid", description="Post a bid", guild=discord.Object(id=GUILD_ID))
async def bid(interaction: discord.Interaction, toon: str, amount: int, user: discord.Member):
    await interaction.response.send_message(f"{toon} {amount} {user.mention}")

bot.run(TOKEN)
