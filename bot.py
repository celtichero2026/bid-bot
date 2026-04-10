import discord
from discord.ext import commands
import os

TOKEN = os.getenv("DISCORD_TOKEN")
LEADER_ROLE_ID = 1415053351116079219

ALLOWED_CHANNEL_IDS = [
    1447764043090755646,
    1447764333894434837,
    1447764834132295782,
    1447765010800578782,
    1447765179172524184,
    1447765439366168687,
]

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

def is_allowed_channel(channel: discord.abc.GuildChannel | discord.Thread) -> bool:
    if channel.id in ALLOWED_CHANNEL_IDS:
        return True

    if isinstance(channel, discord.Thread) and channel.parent_id in ALLOWED_CHANNEL_IDS:
        return True

    return False

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="bid", description="Post a bid")
async def bid(
    interaction: discord.Interaction,
    toon: str,
    amount: int
):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message(
            "Use this in the bid channels only.",
            ephemeral=True
        )
        return

    msg = f"{toon} {amount} @everyone"

    await interaction.response.send_message(
        msg,
        allowed_mentions=discord.AllowedMentions(everyone=True)
    )

@bot.tree.command(name="review", description="Tag leaders for bid review")
async def review(interaction: discord.Interaction, reason: str):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message(
            "Use this in the bid channels only.",
            ephemeral=True
        )
        return

    role = interaction.guild.get_role(LEADER_ROLE_ID)

    if role is None:
        await interaction.response.send_message(
            "Leader role not found in this server",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"{role.mention} Review needed: {reason}",
        allowed_mentions=discord.AllowedMentions(roles=True)
    )

bot.run(TOKEN)
