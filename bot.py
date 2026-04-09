import discord
from discord.ext import commands
import os

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# store message per channel
bid_messages = {}

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="bid", description="Post a bid")
async def bid(interaction: discord.Interaction, toon: str, amount: int, user: discord.Member):
    await interaction.response.send_message(f"{toon} {amount} {user.mention}")
    
    # create or update message
    if channel.id not in bid_messages:
        msg = await channel.send(new_line)
        bid_messages[channel.id] = msg.id
    else:
        try:
            msg = await channel.fetch_message(bid_messages[channel.id])
            await msg.edit(content=msg.content + f"\n{new_line}")
        except:
            msg = await channel.send(new_line)
            bid_messages[channel.id] = msg.id

    # no clutter response
    await interaction.followup.send("✔", ephemeral=True)

TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
