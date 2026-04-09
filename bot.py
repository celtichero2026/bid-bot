import discord
from discord.ext import commands
import os

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

# Example command
@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

# Example bid command (simple)
@bot.command()
async def bid(ctx, amount: int):
    await ctx.send(f"{ctx.author.name} bid {amount}")

# Run bot using environment variable (SAFE)
TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)