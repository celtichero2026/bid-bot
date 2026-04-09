import discord
from discord.ext import commands
import os

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

@bot.tree.command(name="bid", description="Place a bid")
async def bid(interaction: discord.Interaction, amount: int):
    await interaction.response.send_message(f"{interaction.user.name} bid {amount}")

TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
